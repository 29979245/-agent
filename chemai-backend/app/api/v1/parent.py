"""家长端门户 API（design.md D8，挂载于 /api/parent）。

§1 认证+绑定：bind / unbind / children。周报/报告/通知端点见 §2。
/ login 留在 auth.py 的 parent_router 不动（破坏面最小）。
"""
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BusinessRuleViolationError,
    ConflictError,
    NotFoundError,
    ServerError,
)
from app.core.parent_auth import require_bound_child, require_parent
from app.db.models import ParentNotification, Student, StudentParentBinding
from app.db.models.enums import ParentBindingRelation, ParentBindingStatus
from app.db.session import get_db
from app.services.analytics.parent_service import build_parent_report
from app.services.analytics.weekly_report_service import (
    WeeklyReportError,
    generate_ai_summary,
    get_or_generate_weekly_report,
)

parent_router = APIRouter()


class BindRequest(BaseModel):
    student_id: int
    bind_code: str
    relation: ParentBindingRelation = ParentBindingRelation.guardian


@parent_router.post("/bind")
def bind_child(
    payload: BindRequest,
    request: Request,
    db: Session = Depends(get_db),
    parent_id: int = Depends(require_parent),
) -> dict:
    """绑定码建立亲子绑定：匹配 Student.bind_code，成功即消费一次性码。"""
    student = db.get(Student, payload.student_id)
    if student is None:
        raise NotFoundError(detail="学生不存在", error_code="STUDENT_NOT_FOUND")
    if not student.bind_code or payload.bind_code != student.bind_code:
        raise BusinessRuleViolationError(
            detail="绑定码不匹配", error_code="BIND_CODE_MISMATCH", suggestion="请向学生确认当前有效的绑定码"
        )
    existing = (
        db.query(StudentParentBinding)
        .filter(
            StudentParentBinding.parent_id == parent_id,
            StudentParentBinding.student_id == payload.student_id,
        )
        .first()
    )
    if existing is not None:
        if existing.status == ParentBindingStatus.active:
            raise ConflictError(detail="已存在有效的亲子绑定", error_code="BINDING_EXISTS")
        # 复用 inactive 历史行：更新关系/绑定码、恢复 active（D2）
        existing.bind_code = payload.bind_code
        existing.relation = payload.relation
        existing.status = ParentBindingStatus.active
        binding = existing
    else:
        binding = StudentParentBinding(
            parent_id=parent_id,
            student_id=payload.student_id,
            bind_code=payload.bind_code,
            relation=payload.relation,
            status=ParentBindingStatus.active,
        )
        db.add(binding)
    student.bind_code = ""  # 消费一次性绑定码
    db.flush()
    db.commit()
    return {"binding_id": binding.id, "status": "active"}


@parent_router.delete("/bind/{binding_id}")
def unbind_child(
    binding_id: int,
    request: Request,
    db: Session = Depends(get_db),
    parent_id: int = Depends(require_parent),
) -> dict:
    """解绑本人名下绑定（软删置 inactive，D8 保留记录）。"""
    binding = db.get(StudentParentBinding, binding_id)
    if binding is None or binding.parent_id != parent_id:
        # 越权与不存在统一 404，避免枚举他人绑定
        raise NotFoundError(detail="绑定不存在", error_code="BINDING_NOT_FOUND")
    binding.status = ParentBindingStatus.inactive
    db.commit()
    return {"detail": "解绑成功", "binding_id": binding.id}


@parent_router.get("/child/{student_id}/report")
def child_report(
    student_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_bound_child),
) -> dict:
    """家长视角子女报告：绑定校验后聚合，显式不含 bind_code/排名/教师评语。"""
    return build_parent_report(db, student_id)


@parent_router.get("/child/{student_id}/weekly")
def child_weekly(
    student_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_bound_child),
) -> dict:
    """本周周报（懒生成）：当周未生成则调 LLM 生成并缓存。"""
    try:
        return get_or_generate_weekly_report(db, student_id)
    except WeeklyReportError as e:
        raise ServerError(
            detail=str(e), error_code="WEEKLY_REPORT_FAILED", suggestion="请稍后重试"
        )


@parent_router.post("/child/{student_id}/weekly/generate")
def child_weekly_generate(
    student_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_bound_child),
) -> dict:
    """手动触发生成当周周报；周内已存在则返回缓存（去重）。"""
    try:
        return get_or_generate_weekly_report(db, student_id)
    except WeeklyReportError as e:
        raise ServerError(
            detail=str(e), error_code="WEEKLY_REPORT_FAILED", suggestion="请稍后重试"
        )


@parent_router.post("/child/{student_id}/report/ai-summary")
def child_report_ai_summary(
    student_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_bound_child),
) -> dict:
    """AI 通俗解读：对绑定子女报告调用 LLM，遵循 §7 通俗化约束。"""
    report = build_parent_report(db, student_id)
    try:
        return generate_ai_summary(db, student_id, report=report)
    except WeeklyReportError as e:
        raise ServerError(
            detail=str(e), error_code="AI_SUMMARY_FAILED", suggestion="请稍后重试"
        )


@parent_router.get("/children")
def children(
    request: Request,
    db: Session = Depends(get_db),
    parent_id: int = Depends(require_parent),
) -> dict:
    """返回当前家长全部 active 绑定子女概要（学生 ID/姓名/班级名）。"""
    bindings = (
        db.query(StudentParentBinding)
        .filter(
            StudentParentBinding.parent_id == parent_id,
            StudentParentBinding.status == ParentBindingStatus.active,
        )
        .order_by(StudentParentBinding.id)
        .all()
    )
    items = []
    for b in bindings:
        student = db.get(Student, b.student_id)
        if student is None:
            continue
        items.append(
            {
                "student_id": student.id,
                "name": student.name,
                "class_name": student.class_.name if student.class_ else "",
            }
        )
    return {"children": items}


@parent_router.get("/notifications")
def notifications(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    parent_id: int = Depends(require_parent),
) -> dict:
    """通知列表（分页倒序）。parent_id 恒取自 token，忽略 query 传入的 parent_id。"""
    query = db.query(ParentNotification).filter(ParentNotification.parent_id == parent_id)
    total = query.count()
    rows = (
        query.order_by(
            ParentNotification.created_at.desc(),
            ParentNotification.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "notifications": [
            {
                "id": n.id,
                "notification_type": n.notification_type.value,
                "title": n.title,
                "content": n.content,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat(),
            }
            for n in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@parent_router.put("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db),
    parent_id: int = Depends(require_parent),
) -> dict:
    """标记本人通知已读；越权与不存在统一 404，避免枚举他人通知。"""
    n = db.get(ParentNotification, notification_id)
    if n is None or n.parent_id != parent_id:
        raise NotFoundError(detail="通知不存在", error_code="NOTIFICATION_NOT_FOUND")
    n.is_read = True
    db.commit()
    return {"detail": "已标记为已读", "notification_id": n.id, "is_read": True}
