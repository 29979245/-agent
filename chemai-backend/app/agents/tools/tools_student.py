"""学生自助数据工具组——我的错题 / 我的复习任务 / 我的学情（doc 30 §3 学生端）。

学生角色工具，默认操作当前登录学生自身（Account.role_id 解析，见 context.self_student_id），
无需也不允许指定他人；复用既有服务层（WrongQuestionTrainer / SpacedRepetitionEngine /
report_service），零新增数据逻辑。工具返回结构化 dict，由模型转 Markdown 流式输出。
"""
from __future__ import annotations

from pydantic import BaseModel

from app.agents.tools.context import ToolContext, self_student_id, user_role
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models import Question
from app.services.analytics import report_service
from app.services.exercise.spaced_repetition import SpacedRepetitionEngine
from app.services.exercise.wrong_question import WrongQuestionTrainer
from app.services.question.serializers import split_knowledge_points


class ShowMyWrongQuestionsArgs(BaseModel):
    pass


class ShowMyReviewTasksArgs(BaseModel):
    pass


class ShowMyReportArgs(BaseModel):
    pass


def _require_self(ctx: ToolContext) -> int:
    """学生角色解析当前学生 id；非学生/未登录 → ForbiddenError，身份缺失 → NotFoundError。"""
    if ctx.db is None:
        raise ForbiddenError(detail="数据库未注入")
    if user_role(ctx) != "student":
        raise ForbiddenError(
            detail="仅学生可使用该自助工具",
            error_code="STUDENT_TOOL_SELF_ONLY",
            suggestion="请以学生身份登录后使用",
        )
    sid = self_student_id(ctx)
    if sid is None:
        raise NotFoundError(detail="学生账号未绑定学生档案", error_code="STUDENT_PROFILE_NOT_FOUND")
    return sid


def _review_task_dict(ctx: ToolContext, task) -> dict:
    question = ctx.db.get(Question, task.question_id)
    return {
        "review_task_id": task.id,
        "question_id": task.question_id,
        "content": question.content if question else "",
        "options": question.options if question else [],
        "knowledge_points": split_knowledge_points(question.knowledge_points) if question else [],
        "review_level": task.review_level.value,
        "status": task.status.value,
        "next_review_at": task.next_review_at.isoformat() if task.next_review_at else None,
    }


def show_my_wrong_questions(ctx: ToolContext) -> dict:
    """列出当前学生错题（按最近作答倒序、答错次数降序，已掌握移除）。"""
    sid = _require_self(ctx)
    items = WrongQuestionTrainer(ctx.db).list_wrong_questions(sid)
    return {"student_id": sid, "count": len(items), "items": items}


def show_my_review_tasks(ctx: ToolContext) -> dict:
    """列出当前学生到期复习任务（艾宾浩斯间隔复习，pending/overdue 按到期升序）。"""
    sid = _require_self(ctx)
    tasks = SpacedRepetitionEngine(ctx.db).list_due_tasks(sid)
    items = [_review_task_dict(ctx, task) for task in tasks]
    return {"student_id": sid, "count": len(items), "tasks": items}


def show_my_report(ctx: ToolContext) -> dict:
    """返回当前学生学情报告（完成题数/正确率/连续打卡/知识点掌握度/周报/学习计划）。"""
    sid = _require_self(ctx)
    return report_service.build_student_report(ctx.db, sid)
