"""家长报告组工具（doc 30 §3.7，2 工具）——生成报告预览 / 推送周报到家长。

契约要点：
- generate_parent_report：复用 weekly_report_service.generate_weekly_report 聚合练习/诊断/知识点，
  返回家长可读报告预览文本 + requires_confirmation 标记，不发送（不写 ParentNotification）。
- send_report_to_parent：查 StudentParentBinding(status=active) → 逐绑定家长写
  ParentNotification(notification_type=weekly_report) → commit，返回发送确认 + 已通知数；
  报告数据缺省时自动生成。
- 两者均仅 teacher 角色可调用（doc 30 §3.7）；parent 角色调用返回 ForbiddenError。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models import NotificationType, ParentNotification, Student, StudentParentBinding
from app.services.analytics.weekly_report_service import WeeklyReportError, generate_weekly_report
from app.agents.tools.context import ToolContext, user_role
from app.agents.tools.llm_adapter import CompleteAdapter


class GenerateParentReportArgs(BaseModel):
    student_id: int = Field(..., description="学生 ID")


class SendReportToParentArgs(BaseModel):
    student_id: int = Field(..., description="学生 ID")
    report_data: dict = Field(default_factory=dict, description="报告数据（可选，缺省自动生成）")


# ---------------------------------------------------------------- 辅助

def _teacher_check(ctx: ToolContext) -> None:
    """家长报告生成/发送仅教师可操作（doc 30 §3.7）；parent 及其余角色 → ForbiddenError。"""
    if user_role(ctx) != "teacher":
        raise ForbiddenError(detail="仅教师可生成/发送家长报告", error_code="REPORT_TEACHER_ONLY")


def _llm_client(ctx: ToolContext):
    """把 Agent LLMClient 适配为 .complete(messages)；未注入返回 None（服务内回退）。"""
    if ctx.llm is None:
        return None
    return CompleteAdapter(ctx.llm)


def _get_student(ctx: ToolContext, student_id: int) -> dict:
    """取学生；未找到返回 not_found dict。"""
    student = ctx.db.get(Student, student_id)
    if student is None:
        return {"error": "not_found", "message": "未找到该学生", "_guard_error": True}
    return student


# ---------------------------------------------------------------- 工具实现

def generate_parent_report(ctx: ToolContext, student_id: int) -> dict:
    """生成家长可读周报预览（不发送，doc 30 §3.7 工具 15）。"""
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    _teacher_check(ctx)
    student = _get_student(ctx, student_id)
    if isinstance(student, dict):
        return student
    try:
        report = generate_weekly_report(ctx.db, student_id, client=_llm_client(ctx))
    except WeeklyReportError as exc:
        return {"error": "report_failed", "message": str(exc), "_guard_error": True}
    preview = (
        f"{report.get('summary', '')}\n\n{report.get('detail', '')}\n\n"
        f"家庭建议：{report.get('advice', '')}".strip()
    )
    return {
        "student_id": student_id,
        "name": student.name,
        "report_preview": preview,
        "requires_confirmation": True,
        "no_data": bool(report.get("no_data")),
        "report_data": report,
    }


def send_report_to_parent(
    ctx: ToolContext,
    student_id: int,
    report_data: Optional[dict] = None,
) -> dict:
    """推送周报到已绑定家长（doc 30 §3.7 工具 16，审批类写操作）。"""
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    _teacher_check(ctx)
    student = _get_student(ctx, student_id)
    if isinstance(student, dict):
        return student

    # 报告数据缺省时自动生成（best-effort：失败则降级默认文案，不阻塞推送）
    data = dict(report_data or {})
    if not data:
        try:
            data = generate_weekly_report(ctx.db, student_id, client=_llm_client(ctx))
        except WeeklyReportError:
            data = {}
    title = f"{student.name}的本周学习周报"
    content = (
        data.get("summary")
        or data.get("detail")
        or data.get("advice")
        or "本周学习周报已生成，请在学情报告页查看详情。"
    )
    rows = ctx.db.query(StudentParentBinding.parent_id).filter(
        StudentParentBinding.student_id == student_id,
        StudentParentBinding.status == "active",
    ).all()
    for (parent_id,) in rows:
        ctx.db.add(ParentNotification(
            parent_id=parent_id,
            notification_type=NotificationType.weekly_report,
            title=title,
            content=str(content),
        ))
    ctx.db.commit()
    return {
        "sent": True,
        "student_id": student_id,
        "name": student.name,
        "report_title": title,
        "notified_parents": len(rows),
    }
