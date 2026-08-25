"""考试六态状态机（doc 47 / 设计 §6，grilling 定稿）。

转换表：
    draft ─发布(≥1题)▶ published ─首生作答▶ in_progress ─教师触发▶ grading
    grading ─finalize(统计)▶ completed ─教师触发▶ archived（终态只读）

- 非法转换抛 ExamStateError；
- 终态 archived 只读，不可再增删题/发布/归档；
- 可删：仅 draft（发布后删除受限）。
"""
from __future__ import annotations

from app.db.models.enums import ExamStatus


class ExamStateError(ValueError):
    """非法状态转换。"""


# 状态 → 合法后继状态集合（唯一权威转换表）
TRANSITIONS: dict[ExamStatus, set[ExamStatus]] = {
    ExamStatus.draft: {ExamStatus.published},
    ExamStatus.published: {ExamStatus.in_progress},
    ExamStatus.in_progress: {ExamStatus.grading},
    ExamStatus.grading: {ExamStatus.completed},
    ExamStatus.completed: {ExamStatus.archived},
}


def can_transition(current: ExamStatus, target: ExamStatus) -> bool:
    """current 能否直接到达 target。"""
    return target in TRANSITIONS.get(current, set())


def transition(current: ExamStatus, target: ExamStatus) -> ExamStatus:
    """执行转换；非法则抛 ExamStateError。"""
    if not can_transition(current, target):
        raise ExamStateError(f"非法状态转换：{current.value} → {target.value}")
    return target


def is_terminal(status: ExamStatus) -> bool:
    """终态（只读）。"""
    return status == ExamStatus.archived


def is_deletable(status: ExamStatus) -> bool:
    """仅 Draft 可级联删除（发布后删除受限）。"""
    return status == ExamStatus.draft
