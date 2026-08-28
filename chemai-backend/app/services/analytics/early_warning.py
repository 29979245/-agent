"""预警引擎服务：三类检测规则纯函数 + EarlyWarningService 组装（doc 31 §5.1/§5.3）。

- detect_no_login：连续未登录（last_exercise_at 由 max(answered_at) 推导，从未作答用 created_at）
- detect_score_drop：仅正式考试（exam 类型、已发布/完成）最近两场正确率降幅
- detect_high_error_rate：最近一场考试/练习整体错题率
组装：check_all_warnings 遍历全部学生，命中 → 去重创建 WarningLog → 家长通知。
阈值类常量（doc 31 §5.3）：3 天=周末+1 工作日缓冲；0.1≈一个标准差；0.5≈随机猜测线上方。
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    ExamRecord,
    ExamStatus,
    ExamType,
    ParentNotification,
    Student,
    StudentAnswer,
    StudentParentBinding,
    WarningLog,
)
from app.db.models.enums import (
    NotificationType,
    ParentBindingStatus,
    WarningLevel,
    WarningStatus,
    WarningType,
    WebhookEventType,
)
from app.services.integration.webhook_service import emit_event

NO_LOGIN_DAYS = 3
SCORE_DROP_THRESHOLD = 0.1
SCORE_DROP_CRITICAL = 0.2
HIGH_ERROR_RATE_INFO = 0.5
HIGH_ERROR_RATE_WARNING = 0.7
BATCH_LIMIT = 50

PUBLISHED_EXAM_STATUSES = (ExamStatus.published, ExamStatus.completed)

_WARNING_TITLES = {
    WarningType.no_login: "连续未登录预警",
    WarningType.score_drop: "成绩下滑预警",
    WarningType.high_error_rate: "错题率过高预警",
}


# ---------------- 检测规则纯函数（L1 可单测，无 DB） ----------------

def detect_no_login(
    last_exercise_at: Optional[datetime.datetime],
    created_at: Optional[datetime.datetime],
    now: datetime.datetime,
    threshold: int = NO_LOGIN_DAYS,
) -> str | None:
    """连续未登录：最近作答距今 >= threshold 天 → warning；从未作答用 created_at；created_at None 跳过。"""
    if last_exercise_at is not None:
        return "warning" if (now - last_exercise_at).days >= threshold else None
    if created_at is None:
        return None
    return "warning" if (now - created_at).days >= threshold else None


def detect_score_drop(
    points: list[tuple],
    threshold: float = SCORE_DROP_THRESHOLD,
    critical: float = SCORE_DROP_CRITICAL,
) -> str | None:
    """成绩下滑：points 为升序 (date, accuracy)，取最近两场；drop=(prev-recent)/prev。

    不足两场或前次正确率为 0 不触发（除零/数据不足，避免误报）。
    """
    if len(points) < 2:
        return None
    prev_acc = points[-2][1]
    recent_acc = points[-1][1]
    if prev_acc <= 0:
        return None
    drop = (prev_acc - recent_acc) / prev_acc
    if drop >= critical:
        return "critical"
    if drop >= threshold:
        return "warning"
    return None


def detect_high_error_rate(
    error_rate: Optional[float],
    info: float = HIGH_ERROR_RATE_INFO,
    warning: float = HIGH_ERROR_RATE_WARNING,
) -> str | None:
    """错题率过高：>= warning 阈值 → warning；>= info 阈值 → info；无作答（None）跳过。"""
    if error_rate is None:
        return None
    if error_rate >= warning:
        return "warning"
    if error_rate >= info:
        return "info"
    return None


# ---------------- 组装服务 ----------------

class EarlyWarningService:
    """遍历全部学生执行三类检测，去重创建预警并推送家长通知（D3/D4/D5）。"""

    def __init__(self, db: Session):
        self.db = db

    # ---- 检测辅助（单学生查询，作用域小） ----

    def _last_exercise_at(self, student_id: int) -> Optional[datetime.datetime]:
        """最近一次作答时间（max answered_at），从未作答返回 None。"""
        return (
            self.db.query(func.max(StudentAnswer.answered_at))
            .filter(StudentAnswer.student_id == student_id)
            .scalar()
        )

    def _recent_exam_points(self, student_id: int) -> list[tuple]:
        """正式考试成绩点（升序）：学生参与的 exam 类型、published/completed 考试，逐场该生正确率。"""
        exams = (
            self.db.query(ExamRecord)
            .join(StudentAnswer, StudentAnswer.exam_id == ExamRecord.id)
            .filter(
                StudentAnswer.student_id == student_id,
                ExamRecord.exam_type == ExamType.exam,
                ExamRecord.status.in_(PUBLISHED_EXAM_STATUSES),
            )
            .group_by(ExamRecord.id)
            .order_by(ExamRecord.exam_date.asc(), ExamRecord.id.asc())
            .all()
        )
        exam_ids = [e.id for e in exams]
        if not exam_ids:
            return []
        answers = (
            self.db.query(StudentAnswer)
            .filter(StudentAnswer.student_id == student_id, StudentAnswer.exam_id.in_(exam_ids))
            .all()
        )
        by_exam: dict[int, list[StudentAnswer]] = defaultdict(list)
        for a in answers:
            by_exam[a.exam_id].append(a)
        points = []
        for e in exams:
            ans = by_exam.get(e.id, [])
            if not ans:
                continue
            acc = sum(1 for a in ans if a.is_correct) / len(ans)
            points.append((e.exam_date, acc))
        return points

    def _last_exam_error_rate(self, student_id: int) -> Optional[float]:
        """最近一场 published/completed 考试/练习的整体错题率（该生作答）；无作答返回 None。"""
        exam = (
            self.db.query(ExamRecord)
            .join(StudentAnswer, StudentAnswer.exam_id == ExamRecord.id)
            .filter(
                StudentAnswer.student_id == student_id,
                ExamRecord.status.in_(PUBLISHED_EXAM_STATUSES),
            )
            .order_by(ExamRecord.exam_date.desc(), ExamRecord.id.desc())
            .first()
        )
        if exam is None:
            return None
        answers = (
            self.db.query(StudentAnswer)
            .filter(StudentAnswer.student_id == student_id, StudentAnswer.exam_id == exam.id)
            .all()
        )
        if not answers:
            return None
        return sum(1 for a in answers if not a.is_correct) / len(answers)

    # ---- 单学生检查 ----

    def check_student(self, student: Student, now: Optional[datetime.datetime] = None) -> dict:
        """单学生执行三类检测 → 去重创建 + 家长通知，返回 created/by_type/notified。"""
        now = now or datetime.datetime.utcnow()
        created = 0
        notified = 0
        by_type: dict[str, int] = {}
        for wtype, level, data in self._detect_all(student, now):
            warning = self._create_warning(student, wtype, level, data)
            if warning is None:
                continue
            created += 1
            by_type[wtype.value] = by_type.get(wtype.value, 0) + 1
            notified += self._send_warning_notifications(warning, student)
            # warning.triggered 事件（埋点吞异常，不阻断预警主流程）
            emit_event(
                self.db,
                WebhookEventType.warning_triggered,
                {
                    "warning_type": wtype.value,
                    "title": warning.title,
                    "student_id": student.id,
                },
            )
        return {"created": created, "by_type": by_type, "notified": notified}

    def _detect_all(self, student: Student, now: datetime.datetime) -> list[tuple]:
        """返回 [(warning_type, level, data)] 命中规则列表。"""
        hits = []
        last = self._last_exercise_at(student.id)
        no_login_level = detect_no_login(last, student.created_at, now)
        if no_login_level:
            base = last or student.created_at
            hits.append(
                (WarningType.no_login, WarningLevel[no_login_level], {"days": (now - base).days})
            )
        points = self._recent_exam_points(student.id)
        drop_level = detect_score_drop(points)
        if drop_level:
            prev_acc, recent_acc = points[-2][1], points[-1][1]
            hits.append(
                (
                    WarningType.score_drop,
                    WarningLevel[drop_level],
                    {"drop_rate": round((prev_acc - recent_acc) / prev_acc, 4)},
                )
            )
        error_rate = self._last_exam_error_rate(student.id)
        err_level = detect_high_error_rate(error_rate)
        if err_level:
            hits.append(
                (WarningType.high_error_rate, WarningLevel[err_level], {"error_rate": round(error_rate, 4)})
            )
        return hits

    # ---- 去重创建（D4） ----

    def _create_warning(
        self, student: Student, wtype: WarningType, level: WarningLevel, data: dict
    ) -> WarningLog | None:
        """创建前先去重：同 student_id + warning_type + pending 已存在则跳过，返回 None。"""
        existing = (
            self.db.query(WarningLog)
            .filter(
                WarningLog.student_id == student.id,
                WarningLog.warning_type == wtype,
                WarningLog.status == WarningStatus.pending,
            )
            .first()
        )
        if existing is not None:
            return None
        warning = WarningLog(
            student_id=student.id,
            warning_type=wtype,
            level=level,
            title=_WARNING_TITLES[wtype],
            content=_warning_content(student, wtype, data),
            data=data,
        )
        self.db.add(warning)
        self.db.flush()
        return warning

    # ---- 家长通知管道（D5） ----

    def _send_warning_notifications(self, warning: WarningLog, student: Student) -> int:
        """向活跃绑定家长各推一条 ParentNotification(type=warning)；无绑定返回 0。"""
        bindings = (
            self.db.query(StudentParentBinding)
            .filter(
                StudentParentBinding.student_id == student.id,
                StudentParentBinding.status == ParentBindingStatus.active,
            )
            .all()
        )
        count = 0
        for b in bindings:
            self.db.add(
                ParentNotification(
                    parent_id=b.parent_id,
                    notification_type=NotificationType.score_alert,
                    title=warning.title,
                    content=warning.content,
                )
            )
            count += 1
        warning.notified_parent = count > 0
        warning.notified_teacher = True  # 教师经 /api/warning/pending 主动查看
        warning.notified_student = False  # 学生端通知预留
        if count:
            self.db.flush()
        return count

    # ---- 批量检查（D3） ----

    def check_all_warnings(self, now: Optional[datetime.datetime] = None) -> dict:
        """遍历全部学生，分批 flush（≤50），单生失败不阻断，返回 summary。"""
        now = now or datetime.datetime.utcnow()
        students = self.db.query(Student).order_by(Student.id).all()
        summary = {"total": len(students), "created": 0, "by_type": {}, "notified": 0, "failed": 0}
        for i in range(0, len(students), BATCH_LIMIT):
            for student in students[i : i + BATCH_LIMIT]:
                try:
                    result = self.check_student(student, now=now)
                    summary["created"] += result["created"]
                    summary["notified"] += result["notified"]
                    for k, v in result["by_type"].items():
                        summary["by_type"][k] = summary["by_type"].get(k, 0) + v
                except Exception:  # noqa: BLE001 —— 单生失败不阻断后续
                    summary["failed"] += 1
            self.db.flush()
        self.db.commit()
        return summary


def _warning_content(student: Student, wtype: WarningType, data: dict) -> str:
    """预警内容摘要：学生姓名 + 量化指标。"""
    if wtype == WarningType.no_login:
        return f"{student.name} 已连续 {data['days']} 天未登录学习"
    if wtype == WarningType.score_drop:
        return f"{student.name} 最近一次考试成绩下滑 {data['drop_rate'] * 100:.1f}%"
    return f"{student.name} 最近一场考试错题率高达 {data['error_rate'] * 100:.1f}%"
