"""考试状态机测试（4.1）：六态转换表 + 守卫 + 终态/可删判定。"""
import pytest

from app.db.models.enums import ExamStatus
from app.services.question.exam_state import (
    ExamStateError,
    can_transition,
    is_deletable,
    is_terminal,
    transition,
)

ALL = list(ExamStatus)


def test_legal_chain():
    """draft→published→in_progress→grading→completed→archived 全程合法。"""
    chain = [
        ExamStatus.draft,
        ExamStatus.published,
        ExamStatus.in_progress,
        ExamStatus.grading,
        ExamStatus.completed,
        ExamStatus.archived,
    ]
    for cur, nxt in zip(chain, chain[1:]):
        assert can_transition(cur, nxt)
        assert transition(cur, nxt) == nxt


def test_illegal_transitions_rejected():
    """跳过阅卷直接归档、回退、跨态跳跃均非法。"""
    cases = [
        (ExamStatus.draft, ExamStatus.grading),       # 未发布直接阅卷
        (ExamStatus.published, ExamStatus.archived),  # 跳过中间态直接归档
        (ExamStatus.completed, ExamStatus.draft),     # 回退
        (ExamStatus.grading, ExamStatus.published),   # 回退
        (ExamStatus.in_progress, ExamStatus.completed),  # 跳过阅卷
        (ExamStatus.archived, ExamStatus.completed),  # 终态不可逆
    ]
    for cur, nxt in cases:
        assert not can_transition(cur, nxt)
        with pytest.raises(ExamStateError):
            transition(cur, nxt)


def test_terminal_and_deletable():
    assert is_terminal(ExamStatus.archived)
    for s in ALL:
        if s is not ExamStatus.archived:
            assert not is_terminal(s)
    assert is_deletable(ExamStatus.draft)
    for s in ALL:
        if s is not ExamStatus.draft:
            assert not is_deletable(s)


def test_every_state_has_defined_transitions():
    """除终态外每个状态至少有一条出边；终态无出边。"""
    from app.services.question.exam_state import TRANSITIONS

    for s in ALL:
        if is_terminal(s):
            assert not TRANSITIONS.get(s)
        else:
            assert TRANSITIONS.get(s), f"{s} 缺少出边"
