"""预警 API（doc 31 §6.2，design.md D6，挂载于 /api/warning）。

- GET  /pending                             待处理预警列表（可选 class_id 筛选）
- GET  /student/{student_id}                学生预警历史（含已处理/已忽略）
- PUT  /{warning_id}/process                处理预警（processed/ignored + note）
- POST /check                               手动触发全量检查
- GET  /class/{class_id}/summary            班级预警汇总

权限（D7）：warning 资源，student 矩阵默认拒绝 + _ensure_not_student 二次拦截；
班级/学生范围沿用 school_id 组织链隔离（复用面板/诊断隔离模式）。
"""
import datetime
from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.permissions import require_permission
from app.db.models import Class, Grade, Student, Teacher, WarningLog
from app.db.models.enums import WarningLevel, WarningStatus
from app.db.session import get_db
from app.services.analytics.early_warning import EarlyWarningService

from app.api.v1.diagnosis import _require_student_in_teacher_school
from app.api.v1.panel import _ensure_not_student, _require_class_in_teacher_school, _role_id

warning_router = APIRouter()


class ProcessBody(BaseModel):
    action: Literal["processed", "ignored"]
    note: str = ""


def _require_warning_in_teacher_school(db: Session, request: Request, warning: WarningLog) -> None:
    """教师仅可处理本校学生预警；admin+ 不限。"""
    if request.state.user.role == "teacher":
        _require_student_in_teacher_school(db, request, warning.student_id)


def _serialize(w: WarningLog, students: dict | None = None, classes: dict | None = None) -> dict:
    item = {
        "id": w.id,
        "student_id": w.student_id,
        "warning_type": w.warning_type.value,
        "level": w.level.value,
        "title": w.title,
        "content": w.content,
        "data": w.data,
        "status": w.status.value,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "processed_by": w.processed_by,
        "processed_at": w.processed_at.isoformat() if w.processed_at else None,
        "processed_note": w.processed_note,
    }
    if students is not None:
        stu = students.get(w.student_id)
        item["student_name"] = stu.name if stu else ""
        item["class_id"] = stu.class_id if stu else None
        item["class_name"] = (classes or {}).get(stu.class_id, "") if stu else ""
    return item


def _name_maps(db: Session, warnings: list[WarningLog]) -> tuple[dict, dict]:
    """学生/班级名映射（join 学生姓名与班级名）。"""
    student_ids = {w.student_id for w in warnings}
    students = (
        {s.id: s for s in db.query(Student).filter(Student.id.in_(student_ids)).all()}
        if student_ids
        else {}
    )
    class_ids = {s.class_id for s in students.values()}
    classes = (
        {c.id: c.name for c in db.query(Class).filter(Class.id.in_(class_ids)).all()}
        if class_ids
        else {}
    )
    return students, classes


@warning_router.get("/pending")
@require_permission("warning", "read")
def pending_warnings(
    request: Request, class_id: int | None = None, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    query = db.query(WarningLog).filter(WarningLog.status == WarningStatus.pending)
    if class_id is not None:
        _require_class_in_teacher_school(db, request, class_id)
        student_ids = [
            s.id for s in db.query(Student).filter(Student.class_id == class_id).all()
        ]
        query = query.filter(WarningLog.student_id.in_(student_ids)) if student_ids else query.filter(False)
    elif request.state.user.role == "teacher":
        # 未指定班级：教师仅见本校全部 pending（组织链隔离，spec「教师仅可查看本校预警」）
        teacher = db.get(Teacher, _role_id(db, request.state.user))
        if teacher is None:
            raise ForbiddenError()
        school_student_ids = (
            db.query(Student.id)
            .join(Class, Class.id == Student.class_id)
            .join(Grade, Grade.id == Class.grade_id)
            .filter(Grade.school_id == teacher.school_id)
        )
        query = query.filter(WarningLog.student_id.in_(school_student_ids))
    rows = query.order_by(WarningLog.created_at.desc(), WarningLog.id.desc()).all()
    students, classes = _name_maps(db, rows)
    return {"items": [_serialize(w, students, classes) for w in rows]}


@warning_router.get("/student/{student_id}")
@require_permission("warning", "read")
def student_warnings(
    request: Request, student_id: int, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    _require_student_in_teacher_school(db, request, student_id)
    rows = (
        db.query(WarningLog)
        .filter(WarningLog.student_id == student_id)
        .order_by(WarningLog.created_at.desc(), WarningLog.id.desc())
        .all()
    )
    students, classes = _name_maps(db, rows)
    return {"student_id": student_id, "items": [_serialize(w, students, classes) for w in rows]}


@warning_router.put("/{warning_id}/process")
@require_permission("warning", "update")
def process_warning(
    request: Request, warning_id: int, body: ProcessBody, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    warning = db.get(WarningLog, warning_id)
    if warning is None:
        raise NotFoundError()
    _require_warning_in_teacher_school(db, request, warning)

    warning.status = (
        WarningStatus.processed if body.action == "processed" else WarningStatus.ignored
    )
    warning.processed_by = _role_id(db, request.state.user)
    warning.processed_at = datetime.datetime.utcnow()
    warning.processed_note = body.note
    db.commit()
    return {"id": warning.id, "status": warning.status.value}


@warning_router.post("/check")
@require_permission("warning", "create")
def trigger_check(request: Request, db: Session = Depends(get_db)) -> dict:
    _ensure_not_student(request)
    summary = EarlyWarningService(db).check_all_warnings()
    return {"created": summary["created"], "by_type": summary["by_type"], "failed": summary["failed"]}


@warning_router.get("/class/{class_id}/summary")
@require_permission("warning", "read")
def class_summary(
    request: Request, class_id: int, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    _require_class_in_teacher_school(db, request, class_id)
    cls = db.get(Class, class_id)
    student_ids = [
        s.id for s in db.query(Student).filter(Student.class_id == class_id).all()
    ]
    warnings = (
        db.query(WarningLog).filter(WarningLog.student_id.in_(student_ids)).all()
        if student_ids
        else []
    )
    return {
        "class_id": class_id,
        "class_name": cls.name,
        "total": len(warnings),
        "by_type": dict(Counter(w.warning_type.value for w in warnings)),
        "by_level": dict(Counter(w.level.value for w in warnings)),
        "critical_count": sum(1 for w in warnings if w.level == WarningLevel.critical),
    }
