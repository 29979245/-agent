"""记忆组工具（doc 30 §3.6，2 工具）——学生记忆读取 / 教师偏好读取。

契约要点：
- memory_student_get：读长期存储诊断历史（最近 5 条）+ 学生当前学习计划；全体角色可用，
  student 角色仅可读自身（Account.role_id 指向 Student.id，跨生返回 ForbiddenError），
  parent 角色仅可读已绑定（StudentParentBinding active）子女。
- memory_teacher_get：读教师偏好（LongTermStore.teacher_pref）；仅 teacher 可调用。
- 读取来源 LongTermStore（ctx.memory.long_term）；写入路径由诊断完成点接线（tasks 3.2，
  push_student_diagnosis_memory 供 tools_diagnosis / tools_ocr 复用）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.exceptions import ForbiddenError
from app.db.models import Account, Student, StudentParentBinding
from app.services.diagnosis.aggregation import normalize_profile
from app.agents.tools.context import ToolContext, self_student_id, user_id, user_role

_DOMINANT_LABELS = {"concept": "概念理解", "reading": "审题障碍", "expression": "表述障碍"}


class MemoryStudentGetArgs(BaseModel):
    student_id: Optional[int] = Field(default=None, description="学生 ID（学生角色可缺省，自动取本人）")


class MemoryTeacherGetArgs(BaseModel):
    teacher_id: Optional[int] = Field(default=None, description="教师 ID（可选，缺省按登录角色）")


# ---------------------------------------------------------------- 辅助

def _bound_student_ids(ctx: ToolContext) -> set[int]:
    """家长角色可访问的子女 id 集合（StudentParentBinding active）。

    身份链：user_id = Account.id → Account.role_id = Parent.id → 绑定表。
    """
    if ctx.db is None or user_role(ctx) != "parent":
        return set()
    account = ctx.db.get(Account, user_id(ctx))
    if account is None:
        return set()
    rows = ctx.db.query(StudentParentBinding.student_id).filter(
        StudentParentBinding.parent_id == account.role_id,
        StudentParentBinding.status == "active",
    ).all()
    return {row[0] for row in rows}


def _assert_access_ok(ctx: ToolContext, student_id: int) -> None:
    """角色级读取门控：student 仅自身、parent 仅绑定子女；其余角色（teacher/tutor）不设限。"""
    role = user_role(ctx)
    if role == "student":
        if student_id != self_student_id(ctx):
            raise ForbiddenError(
                detail="学生仅可读取自己的记忆",
                error_code="MEMORY_SELF_ONLY",
                suggestion="请使用本人学生 ID 查询",
            )
    elif role == "parent":
        if student_id not in _bound_student_ids(ctx):
            raise ForbiddenError(
                detail="家长仅可查看已绑定子女的记忆",
                error_code="MEMORY_PARENT_CHILD_ONLY",
                suggestion="仅可查询已绑定子女的学情",
            )


def _long_term(ctx: ToolContext):
    """取 LongTermStore；未注入返回 None（读端对空历史返回空列表而非报错）。"""
    if ctx.memory is None:
        return None
    return getattr(ctx.memory, "long_term", None)


def push_student_diagnosis_memory(ctx: ToolContext, student_id: int, *, source: str) -> bool:
    """把学生最新障碍画像写入长期记忆（best-effort，失败不阻塞主流程）。

    D3 写接线：diagnose_barrier 个体诊断、OCR 保存触发诊断后调用，
    供 memory_student_get 读回（spec「诊断写记忆」场景）。返回是否写入成功。
    """
    try:
        store = _long_term(ctx)
        if ctx.db is None or store is None:
            return False
        student = ctx.db.get(Student, student_id)
        if student is None:
            return False
        profile = normalize_profile(student.barrier_profile)
        dominant = max(profile, key=profile.get) if profile else "concept"
        return bool(store.push_student_diagnosis(
            student_id,
            {
                "ts": datetime.utcnow().isoformat(),
                "source": source,
                "barrier_profile": profile,
                "dominant_barrier": dominant,
                "dominant_label": _DOMINANT_LABELS.get(dominant, dominant),
            },
        ))
    except Exception:  # noqa: BLE001 —— 写记忆 best-effort，失败不阻塞
        return False


# ---------------------------------------------------------------- 工具实现

def memory_student_get(ctx: ToolContext, student_id: Optional[int] = None) -> dict:
    """读取学生诊断历史（最近 5 条）与当前学习计划（doc 30 §3.6 工具 13）。

    学生角色可缺省 student_id：自动解析本人（Account.role_id）；教师/家长需显式指定。
    """
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    if student_id is None:
        student_id = self_student_id(ctx)
        if student_id is None:
            return {"error": "not_found", "message": "无法解析当前学生，请显式提供学生 ID", "_guard_error": True}
    _assert_access_ok(ctx, student_id)
    student = ctx.db.get(Student, student_id)
    if student is None:
        return {"error": "not_found", "message": "未找到该学生", "_guard_error": True}
    store = _long_term(ctx)
    history = store.student_diagnosis_history(student_id) if store is not None else []
    return {
        "student_id": student_id,
        "name": student.name,
        "diagnosis_history": history,
        "learning_plan": student.learning_plan or {},
    }


def memory_teacher_get(
    ctx: ToolContext,
    teacher_id: Optional[int] = None,
) -> dict:
    """读取教师偏好设置（教学风格/难度偏好/班级配置，doc 30 §3.6 工具 14）；仅 teacher。"""
    if user_role(ctx) != "teacher":
        raise ForbiddenError(
            detail="仅教师可读取偏好设置",
            error_code="MEMORY_TEACHER_ONLY",
            suggestion="教师偏好属于教师配置，其他角色不可读取",
        )
    store = _long_term(ctx)
    if store is None:
        return {"error": "memory_unavailable", "message": "长期记忆未注入", "_guard_error": True}
    tid = teacher_id if teacher_id is not None else user_id(ctx)
    return {
        "teacher_id": tid,
        "pref": store.teacher_pref(tid, {}),
    }
