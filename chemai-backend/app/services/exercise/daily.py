"""每日练习调度服务（doc 28 §6 与 29 §9，design.md D7/D8）。

- 每日 08:00 UTC 批量：障碍画像 → 知识点映射 → ZPD 抽样生成 per-student 每日练习
  （同生同天去重，分批 ≤5 处理，单学生失败不阻断后续）
- 家长通知：绑定家长发 ParentNotification，无绑定静默跳过
- 超期标记：pending 且过 next_review_at → overdue
"""
from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import (
    ExamRecord,
    ExamStatus,
    ExamType,
    ParentNotification,
    Student,
    StudentParentBinding,
)
from app.db.models.enums import NotificationType, ParentBindingStatus
from app.services.exercise.adaptive import AdaptivePracticeService, copy_historical_question
from app.services.exercise.sampling import sample_questions
from app.services.exercise.spaced_repetition import SpacedRepetitionEngine
from app.services.question.historical import HistoricalBank, get_bank

DAILY_NAME = "每日练习"
BATCH_LIMIT = 5
DAILY_COUNT = 3


class DailyPracticeScheduler:
    """每日批量：生成每日练习 → 通知家长 → 标记超期。"""

    def __init__(self, db: Session, bank: HistoricalBank | None = None):
        self.db = db
        self.bank = bank or get_bank()

    # ---- 单学生每日练习 ----

    def create_daily_practice(
        self, student: Student, now: Optional[datetime.datetime] = None,
        skip_same_day_check: bool = False,
    ) -> dict | None:
        """生成一份每日练习；同生同天已存在每日练习则跳过（返回 None）。

        skip_same_day_check=True 供学生端「生成新练习」按需加练：忽略同天去重，
        允许当天再生成一份（学生显式请求，不受调度器一次性限制，spec daily-practice c1 管调度批次）。
        """
        today = (now or datetime.datetime.utcnow()).date()
        # 只按 mode=="daily" 去重：同一天训练/变式记录不阻断每日布置（spec daily-practice c1）
        if not skip_same_day_check:
            existing = (
                self.db.query(ExamRecord)
                .filter(
                    ExamRecord.student_id == student.id,
                    ExamRecord.exam_type == ExamType.practice,
                    ExamRecord.exam_date == today,
                )
                .all()
            )
            if any((e.question_stats or {}).get("mode") == "daily" for e in existing):
                return None
        # 目标解析收敛：复用 adaptive.plan_for（ZPD + 障碍 + 薄弱点补齐），避免分叉
        plan = AdaptivePracticeService(self.db, self.bank).plan_for(student)
        barrier = plan["barrier"]
        zpd = plan["zpd_difficulty"]
        difficulty = plan["difficulty"]
        kps = plan["knowledge_points"]
        selected, shortfall = sample_questions(
            self.bank, kps, difficulty, count=DAILY_COUNT, choice_only=True
        )
        exam = ExamRecord(
            class_id=None,
            student_id=student.id,
            name=DAILY_NAME,
            exam_type=ExamType.practice,
            status=ExamStatus.published,
            exam_date=today,
            question_stats={
                "mode": "daily",
                "difficulty": difficulty,
                "zpd_difficulty": zpd,
                "barrier": barrier,
                "deadline": (today + datetime.timedelta(days=1)).isoformat(),
            },
        )
        self.db.add(exam)
        self.db.flush()
        for ref, hq in selected:
            self.db.add(copy_historical_question(self.db, hq, exam.id, difficulty))
        self.db.flush()
        return {
            "student_id": student.id,
            "exam_id": exam.id,
            "question_count": len(selected),
            "shortfall": shortfall,
            "difficulty": difficulty,
            "barrier": barrier,
        }

    # ---- 家长通知 ----

    def notify_parent(self, student: Student, exam: ExamRecord) -> int:
        """向绑定家长发练习布置通知；无绑定静默跳过，返回通知数。"""
        binding = (
            self.db.query(StudentParentBinding)
            .filter(
                StudentParentBinding.student_id == student.id,
                StudentParentBinding.status == ParentBindingStatus.active,
            )
            .first()
        )
        if binding is None:
            return 0
        self.db.add(ParentNotification(
            parent_id=binding.parent_id,
            notification_type=NotificationType.message,
            title="今日练习已布置",
            content=f"{student.name} 的每日练习《{exam.name}》已布置，请关注完成情况。",
        ))
        self.db.flush()
        return 1

    # ---- 批量执行 ----

    def run_daily_batch(
        self, now: Optional[datetime.datetime] = None,
    ) -> dict:
        """遍历学生分批（≤5）执行；单学生失败不阻断。"""
        now = now or datetime.datetime.utcnow()
        students = self.db.query(Student).order_by(Student.id).all()
        engine = SpacedRepetitionEngine(self.db)
        summary = {"total": len(students), "created": 0, "skipped": 0,
                   "notified": 0, "overdue": 0, "failed": 0}
        for i in range(0, len(students), BATCH_LIMIT):
            for student in students[i : i + BATCH_LIMIT]:
                try:
                    result = self.create_daily_practice(student, now=now)
                    if result is None:
                        summary["skipped"] += 1
                    else:
                        summary["created"] += 1
                        summary["notified"] += self.notify_parent(student, self.db.get(ExamRecord, result["exam_id"]))
                    summary["overdue"] += engine.mark_overdue(student.id, now=now)
                except Exception:  # noqa: BLE001 —— 单学生失败不阻断后续
                    summary["failed"] += 1
            self.db.flush()
        self.db.commit()
        return summary
