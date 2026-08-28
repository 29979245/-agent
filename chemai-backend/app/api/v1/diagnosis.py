"""障碍诊断 API（doc 48 §8，挂载于 /api/diagnosis）。

- POST /run-llm            批量触发 LLM 深度诊断（≤10 条、barrier_type IS NULL 优先、
  ThreadPoolExecutor(max_workers=5) 子线程仅 LLM IO、主线程单事务落库 + 聚合、失败持久化可重跑）
- GET  /barrier/{class_id}/{exam_id}  班级指定考试三维障碍分布（无数据回退历史画像）
- GET  /class/{id}/stats              班级整体诊断统计
- GET  /class/{id}/kp/{kp}            指定知识点障碍分布（JOIN question）
- GET  /history/{student_id}          单个学生诊断历史（student 仅读自身）
- PUT  /override/{student_id}         教师覆盖画像（90/5/5 权重 + 留痕 + 冻结）
- DELETE /override/{student_id}       解除冻结（教师显式清除，恢复聚合）
- GET  /override/{student_id}         覆盖历史
- GET/PUT /config/{teacher_id}        诊断阈值配置（upsert、部分更新、默认 3/2/3/3/false）
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import require_permission
from app.db.models import (
    Account,
    BarrierConfig,
    BarrierOverrideLog,
    Class,
    ExamRecord,
    Grade,
    Question,
    Student,
    StudentAnswer,
    Teacher,
)
from app.db.session import get_db
from app.services.diagnosis.aggregation import normalize_profile, refresh_profiles
from app.services.diagnosis.fusion import FusionResult, fuse
from app.services.diagnosis.llm_diagnosis import (
    DiagnosisLLMClient,
    FallbackDiagnosisLLMClient,
    diagnose_llm,
)
from app.services.diagnosis.rule_engine import ChemistryRuleEngine

diagnosis_logger = logging.getLogger("chemai.diagnosis")
diagnosis_router = APIRouter()

MAX_BATCH = 10
MAX_WORKERS = 5
DIAGNOSIS_VERSION = "1.0"
BARRIER_AXES = ("concept", "reading", "expression")
DEFAULT_CONFIG = {
    "consecutive_error_threshold": 3,
    "consecutive_correct_threshold": 2,
    "low_score_threshold": 3,
    "warning_threshold": 3,
    "enabled": False,
}
DEFAULT_OVERRIDE_WEIGHTS = [90, 5, 5]

_engine = ChemistryRuleEngine()


def get_diagnosis_client() -> DiagnosisLLMClient:
    """诊断 LLM 客户端（FastAPI 依赖，测试可覆盖注入 Mock）。"""
    return FallbackDiagnosisLLMClient()


class RunLLMRequest(BaseModel):
    exam_id: int
    limit: int = Field(default=10, ge=1, le=10)


class OverrideRequest(BaseModel):
    barrier_type: str = Field(..., pattern="^(concept|reading|expression)$")
    weights: list[int] = Field(default=DEFAULT_OVERRIDE_WEIGHTS, min_length=3, max_length=3)
    reason: str = Field(..., min_length=1, max_length=500)


class ConfigRequest(BaseModel):
    consecutive_error_threshold: int | None = Field(default=None, ge=0)
    consecutive_correct_threshold: int | None = Field(default=None, ge=0)
    low_score_threshold: int | None = Field(default=None, ge=0)
    warning_threshold: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


# ---------------- 辅助 ----------------

def _bt_str(bt) -> str | None:
    if bt is None:
        return None
    return bt.value if hasattr(bt, "value") else str(bt)


def _role_id(db: Session, user) -> int:
    """把 account.id 解析为业务实体 id（teacher.id / student.id）。"""
    account = db.get(Account, user.user_id)
    return account.role_id if account else user.user_id


def _pad_dist(dist: dict) -> dict:
    return {axis: dist.get(axis, {"count": 0, "pct": 0.0}) for axis in BARRIER_AXES}


def _ensure_not_student(request: Request) -> None:
    """统计端点仅限教师+（admin/dept_admin/subject_lead/teacher）：学生仅读自身 history（spec 权限场景）。"""
    if request.state.user.role == "student":
        raise ForbiddenError()


def _require_student_in_teacher_school(db: Session, request: Request, student_id: int) -> None:
    """教师仅可操作本校学生的画像（组织链数据隔离）；admin+ 不限。跨校访问即 403。"""
    user = request.state.user
    if user.role != "teacher":
        return
    school_id = (
        db.query(Grade.school_id)
        .select_from(Student)
        .join(Class, Class.id == Student.class_id)
        .join(Grade, Grade.id == Class.grade_id)
        .filter(Student.id == student_id)
        .scalar()
    )
    teacher = db.get(Teacher, _role_id(db, user))
    if school_id is None or teacher is None or teacher.school_id != school_id:
        raise ForbiddenError()


def _require_own_config(db: Session, request: Request, teacher_id: int) -> None:
    """教师仅可读写自身诊断配置；admin+ 可管理任意教师配置。"""
    if request.state.user.role == "teacher" and _role_id(db, request.state.user) != teacher_id:
        raise ForbiddenError()


def _weights_to_profile(weights: list[int]) -> dict:
    total = sum(weights) or 1
    return {axis: round(w / total, 2) for axis, w in zip(BARRIER_AXES, weights)}


def _recent_history(db: Session, student_id: int, limit: int = 5) -> list[str]:
    rows = (
        db.query(StudentAnswer)
        .filter(StudentAnswer.student_id == student_id, StudentAnswer.is_correct.is_(False))
        .order_by(StudentAnswer.id.desc())
        .limit(limit)
        .all()
    )
    return [a.answer_text[:200] for a in rows]


def _student_grade(db: Session, student_id: int) -> str | None:
    student = db.get(Student, student_id)
    if student is None:
        return None
    cls = db.get(Class, student.class_id)
    if cls is None:
        return None
    grade = db.get(Grade, cls.grade_id)
    return grade.name if grade else None


def _apply_to_answer(ans: StudentAnswer, fused: FusionResult) -> None:
    """把融合结果平铺到 StudentAnswer 落库列；失败写 error 标志（8.1a）。"""
    ans.fused_conf = fused.fused_conf
    ans.rule_conf = fused.rule_conf
    ans.llm_conf = fused.llm_conf
    ans.diagnosis_flag = fused.diagnosis_flag
    ans.diagnosis_version = DIAGNOSIS_VERSION
    ans.diagnosis_source = fused.source
    detail: dict = {
        "reasoning": fused.reasoning,
        "suggestion": fused.suggestion,
        "remediation": fused.remediation,
    }
    if fused.barrier_type is not None:
        ans.barrier_type = fused.barrier_type
    if fused.diagnosis_flag == "error":
        detail["error"] = fused.detail.get("error") or "诊断失败"
        ans.barrier_type = None
    ans.diagnosis_detail = detail


def _apply_config_flags(db: Session, teacher_id: int, answers: list[StudentAnswer]) -> None:
    """config 接线（8.3a）：启用且连续错误 ≥ 阈值 → diagnosis_flag=needs_attention。"""
    cfg = db.query(BarrierConfig).filter(BarrierConfig.teacher_id == teacher_id).first()
    if cfg is None or not cfg.enabled:
        return
    for ans in answers:
        if ans.diagnosis_flag == "error":
            continue  # 失败条保持 error，不被预警标记覆盖
        if ans.consecutive_errors >= cfg.consecutive_error_threshold:
            ans.diagnosis_flag = "needs_attention"


# ---------------- 8.1 run-llm ----------------


def run_llm_batch(
    db: Session,
    exam_id: int,
    client: DiagnosisLLMClient,
    user,
    limit: int = MAX_BATCH,
) -> dict:
    """批量触发 LLM 深度诊断核心逻辑：错误作答 barrier_type IS NULL 优先，≤10 条。

    端点与 OCR 保存后诊断（8.1）共用；user 提供教师 role_id 供 config 接线。
    """
    base_filter = (
        StudentAnswer.exam_id == exam_id,
        StudentAnswer.is_correct.is_(False),
        StudentAnswer.barrier_type.is_(None),
    )
    total_pending = (
        db.query(func.count(StudentAnswer.id)).filter(*base_filter).scalar() or 0
    )
    candidates = (
        db.query(StudentAnswer)
        .filter(*base_filter)
        .order_by(StudentAnswer.id)
        .limit(min(limit, MAX_BATCH))
        .all()
    )
    if not candidates:
        return {
            "exam_id": exam_id,
            "diagnosed": 0,
            "failed": 0,
            "failures": [],
            "remaining": 0,
            "message": "无待诊断数据",
        }

    # 主线程构建输入（DB 读）+ 规则引擎判定（纯计算）
    tasks: list[dict] = []
    for ans in candidates:
        question = db.get(Question, ans.question_id)
        content = question.content if question else ""
        tasks.append(
            {
                "answer": ans,
                "inputs": {
                    "question": content,
                    "answer": ans.answer_text,
                    "correct_answer": question.answer if question else "",
                    "history": _recent_history(db, ans.student_id),
                    "grade": _student_grade(db, ans.student_id),
                    "avg_score": None,
                    "mastery_level": None,
                },
                "rule": _engine.top_diagnosis(ans.answer_text, content),
            }
        )

    # 子线程仅 LLM IO（不触 DB）；主线程收集
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_map = {
            pool.submit(diagnose_llm, t["inputs"], client): i for i, t in enumerate(tasks)
        }
        llm_results = {i: fut.result() for i, fut in enumerate(future_map)}

    # 主线程单事务：融合 → 落库 → 聚合
    diagnosed, failed = 0, 0
    failures: list[dict] = []
    for i, task in enumerate(tasks):
        ans = task["answer"]
        llm_res = llm_results[i]
        fused = fuse(task["rule"], llm_res, question_id=ans.question_id)
        is_failure = fused.diagnosis_flag == "error" or (
            llm_res is not None and llm_res.is_error
        )
        if is_failure:
            # 失败持久化（8.1a）：barrier_type 保持 NULL 可重跑，标志 error + 原因留痕
            failed += 1
            reason = (llm_res.error if llm_res and llm_res.is_error else "") or "融合失败无有效诊断"
            failures.append(
                {"student_id": ans.student_id, "question_id": ans.question_id, "reason": reason}
            )
            ans.barrier_type = None
            ans.fused_conf = fused.fused_conf
            ans.rule_conf = fused.rule_conf
            ans.llm_conf = fused.llm_conf
            ans.diagnosis_flag = "error"
            ans.diagnosis_version = DIAGNOSIS_VERSION
            ans.diagnosis_source = "error"
            detail = {
                "reasoning": fused.reasoning,
                "suggestion": fused.suggestion,
                "remediation": fused.remediation,
            }
            detail["error"] = reason
            ans.diagnosis_detail = detail
        else:
            _apply_to_answer(ans, fused)
            diagnosed += 1

    _apply_config_flags(db, _role_id(db, user), candidates)
    db.commit()
    refresh_profiles(db)  # 聚合跳过冻结学生（T1）

    return {
        "exam_id": exam_id,
        "diagnosed": diagnosed,
        "failed": failed,
        "failures": failures,
        "remaining": max(0, total_pending - len(candidates)),
        "message": f"还有 {total_pending - len(candidates)} 条待分批处理" if total_pending > len(candidates) else None,
    }


@diagnosis_router.post("/run-llm")
@require_permission("diagnosis", "create")
def run_llm(
    request: Request,
    payload: RunLLMRequest,
    db: Session = Depends(get_db),
    client: DiagnosisLLMClient = Depends(get_diagnosis_client),
) -> dict:
    """批量触发 LLM 深度诊断：错误作答 barrier_type IS NULL 优先，≤10 条。"""
    return run_llm_batch(db, payload.exam_id, client, request.state.user, limit=payload.limit)


# ---------------- 8.2 只读统计 ----------------

@diagnosis_router.get("/barrier/{class_id}/{exam_id}")
@require_permission("diagnosis", "read")
def barrier_distribution(
    request: Request, class_id: int, exam_id: int, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    rows = (
        db.query(StudentAnswer.barrier_type, func.count(StudentAnswer.id))
        .join(ExamRecord, StudentAnswer.exam_id == ExamRecord.id)
        .filter(
            ExamRecord.id == exam_id,
            ExamRecord.class_id == class_id,
            StudentAnswer.barrier_type.isnot(None),
        )
        .group_by(StudentAnswer.barrier_type)
        .all()
    )
    total = sum(count for _, count in rows)
    if total > 0:
        dist = {
            _bt_str(bt): {"count": count, "pct": round(count / total, 2)}
            for bt, count in rows
        }
        return {
            "class_id": class_id,
            "exam_id": exam_id,
            "distribution": _pad_dist(dist),
            "source": "current",
            "denominator": total,
        }
    return _historical_distribution(db, class_id, exam_id)


def _historical_distribution(db: Session, class_id: int, exam_id: int) -> dict:
    """当前考试无数据 → 回退聚合班级学生历史 barrier_profile（按主障碍计数）。"""
    students = db.query(Student).filter(Student.class_id == class_id).all()
    counts = {axis: 0 for axis in BARRIER_AXES}
    profiled = 0
    for s in students:
        profile = normalize_profile(s.barrier_profile)
        dominant = max(profile, key=profile.get)
        if profile[dominant] > 0:
            counts[dominant] += 1
            profiled += 1
    dist = {
        axis: {"count": counts[axis], "pct": round(counts[axis] / profiled, 2) if profiled else 0.0}
        for axis in BARRIER_AXES
    }
    return {
        "class_id": class_id,
        "exam_id": exam_id,
        "distribution": dist,
        "source": "historical",
        "denominator": profiled,
    }


@diagnosis_router.get("/class/{class_id}/stats")
@require_permission("diagnosis", "read")
def class_stats(request: Request, class_id: int, db: Session = Depends(get_db)) -> dict:
    _ensure_not_student(request)
    students = db.query(Student).filter(Student.class_id == class_id).all()
    total = len(students)
    profiled = 0
    sums = {axis: 0.0 for axis in BARRIER_AXES}
    for s in students:
        profile = normalize_profile(s.barrier_profile)
        if any(v > 0 for v in profile.values()):
            profiled += 1
        for axis in BARRIER_AXES:
            sums[axis] += profile[axis]
    status_rows = (
        db.query(StudentAnswer.diagnosis_flag, func.count(StudentAnswer.id))
        .join(ExamRecord, StudentAnswer.exam_id == ExamRecord.id)
        .filter(ExamRecord.class_id == class_id)
        .group_by(StudentAnswer.diagnosis_flag)
        .all()
    )
    return {
        "class_id": class_id,
        "total_students": total,
        "profiled_students": profiled,
        "avg_profile": {axis: round(sums[axis] / total, 2) if total else 0.0 for axis in BARRIER_AXES},
        "status_counts": {flag or "none": count for flag, count in status_rows},
    }


@diagnosis_router.get("/class/{class_id}/kp/{kp}")
@require_permission("diagnosis", "read")
def kp_stats(request: Request, class_id: int, kp: str, db: Session = Depends(get_db)) -> dict:
    """指定知识点障碍分布（8.4a：JOIN question 表按 knowledge_points 关联）。"""
    _ensure_not_student(request)
    rows = (
        db.query(StudentAnswer.barrier_type, func.count(StudentAnswer.id))
        .join(Question, StudentAnswer.question_id == Question.id)
        .join(ExamRecord, StudentAnswer.exam_id == ExamRecord.id)
        .filter(
            ExamRecord.class_id == class_id,
            Question.knowledge_points.contains(kp),
            StudentAnswer.barrier_type.isnot(None),
        )
        .group_by(StudentAnswer.barrier_type)
        .all()
    )
    total = sum(count for _, count in rows)
    dist = {
        _bt_str(bt): {"count": count, "pct": round(count / total, 2) if total else 0.0}
        for bt, count in rows
    }
    return {
        "class_id": class_id,
        "kp": kp,
        "distribution": _pad_dist(dist),
        "total": total,
    }


@diagnosis_router.get("/history/{student_id}")
@require_permission("diagnosis", "read")
def student_history(
    request: Request, student_id: int, db: Session = Depends(get_db)
) -> dict:
    user = request.state.user
    if user.role == "student" and _role_id(db, user) != student_id:
        raise ForbiddenError()  # 学生仅读自身历史
    rows = (
        db.query(StudentAnswer, Question)
        .join(Question, StudentAnswer.question_id == Question.id)
        .filter(StudentAnswer.student_id == student_id, StudentAnswer.barrier_type.isnot(None))
        .order_by(StudentAnswer.id.desc())
        .all()
    )
    history = [
        {
            "student_id": ans.student_id,
            "question_id": ans.question_id,
            "question_content": q.content,
            "answer_text": ans.answer_text,
            "is_correct": ans.is_correct,
            "barrier_type": _bt_str(ans.barrier_type),
            "fused_conf": ans.fused_conf,
            "diagnosis_flag": ans.diagnosis_flag,
            "diagnosis_source": ans.diagnosis_source,
            "diagnosis_detail": ans.diagnosis_detail,
        }
        for ans, q in rows
    ]
    return {"student_id": student_id, "count": len(history), "history": history}


# ---------------- 8.3 教师覆盖与配置 ----------------

@diagnosis_router.put("/override/{student_id}")
@require_permission("diagnosis", "update")
def override_student(
    request: Request,
    student_id: int,
    payload: OverrideRequest,
    db: Session = Depends(get_db),
) -> dict:
    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError()
    _require_student_in_teacher_school(db, request, student_id)
    teacher_id = _role_id(db, request.state.user)
    before = dict(student.barrier_profile) if isinstance(student.barrier_profile, dict) else {}
    after = _weights_to_profile(payload.weights)
    student.barrier_profile = after
    student.barrier_last_updated = datetime.utcnow()
    student.barrier_frozen = True  # 覆盖后冻结，聚合跳过
    db.add(
        BarrierOverrideLog(
            teacher_id=teacher_id,
            student_id=student.id,
            before=before,
            after=after,
            reason=payload.reason,
        )
    )
    db.commit()
    return {
        "student_id": student.id,
        "barrier_type": payload.barrier_type,
        "profile": after,
        "frozen": True,
    }


@diagnosis_router.delete("/override/{student_id}")
@require_permission("diagnosis", "update")
def unfreeze_student(request: Request, student_id: int, db: Session = Depends(get_db)) -> dict:
    """教师显式解除冻结（恢复聚合覆盖）。"""
    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError()
    _require_student_in_teacher_school(db, request, student_id)
    student.barrier_frozen = False
    db.commit()
    return {"student_id": student.id, "frozen": False}


@diagnosis_router.get("/override/{student_id}")
@require_permission("diagnosis", "read")
def override_history(request: Request, student_id: int, db: Session = Depends(get_db)) -> dict:
    _require_student_in_teacher_school(db, request, student_id)
    logs = (
        db.query(BarrierOverrideLog)
        .filter(BarrierOverrideLog.student_id == student_id)
        .order_by(BarrierOverrideLog.created_at.desc())
        .all()
    )
    return {
        "student_id": student_id,
        "logs": [
            {
                "id": log.id,
                "teacher_id": log.teacher_id,
                "before": log.before,
                "after": log.after,
                "reason": log.reason,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }


@diagnosis_router.get("/config/{teacher_id}")
@require_permission("diagnosis", "read")
def get_config(request: Request, teacher_id: int, db: Session = Depends(get_db)) -> dict:
    _require_own_config(db, request, teacher_id)
    cfg = db.query(BarrierConfig).filter(BarrierConfig.teacher_id == teacher_id).first()
    data = {k: (getattr(cfg, k) if cfg else DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    return {"teacher_id": teacher_id, **data}


@diagnosis_router.put("/config/{teacher_id}")
@require_permission("diagnosis", "update")
def put_config(
    request: Request,
    teacher_id: int,
    payload: ConfigRequest,
    db: Session = Depends(get_db),
) -> dict:
    _require_own_config(db, request, teacher_id)
    cfg = db.query(BarrierConfig).filter(BarrierConfig.teacher_id == teacher_id).first()
    if cfg is None:
        cfg = BarrierConfig(teacher_id=teacher_id)
        db.add(cfg)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(cfg, key, value)
    db.commit()
    return {"teacher_id": teacher_id, **{k: getattr(cfg, k) for k in DEFAULT_CONFIG}}
