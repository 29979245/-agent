"""UploadSession 状态机单元测试（task 2.1）：状态转移 + 终态守卫 + 非法迁移拒绝 + 版本递增。

状态机为纯函数：transfer(session_status, target, version) → (new_status, version+1)。
"""
import pytest

from app.db.models.enums import UploadSessionStatus as S
from app.services.ocr.state_machine import (
    TRANSITIONS,
    advance_to,
    transfer,
)


def test_normal_grading_flow_full_chain():
    """正常批改流转：uploaded→previewing→ready→grading→graded→done，逐级递增版本。"""
    status, version = S.uploaded, 0
    for expected in (S.previewing, S.ready, S.grading, S.graded, S.done):
        status, version = transfer(status, expected, version)
        assert status == expected
    assert version == 5
    assert status == S.done


def test_import_branch_chain():
    """导入分支：ready→importing→imported→done。"""
    status, version = S.ready, 2
    for expected in (S.importing, S.imported, S.done):
        status, version = transfer(status, expected, version)
        assert status == expected
    assert version == 5


def test_version_increments_by_one_per_transfer():
    status, version = transfer(S.uploaded, S.previewing, 7)
    assert version == 8


@pytest.mark.parametrize("terminal", [S.done, S.discarded, S.error])
def test_terminal_guard_rejects_any_change(terminal):
    """终态守卫：done/discarded/error 任一终态下执行状态变更抛异常。"""
    with pytest.raises(ValueError):
        transfer(terminal, S.uploaded, 0)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.uploaded, S.done),      # 跳过中间态
        (S.uploaded, S.grading),   # 未达 ready 直接批改
        (S.ready, S.graded),       # 分支内部越级
        (S.previewing, S.previewing),  # 自迁移
        (S.importing, S.grading),  # 分支互串
        (S.imported, S.graded),
    ],
)
def test_illegal_transition_rejected(current, target):
    """非法迁移拒绝：目标不在当前状态可达集合时抛异常。"""
    with pytest.raises(ValueError):
        transfer(current, target, 0)


def test_transitions_map_covers_all_ten_states():
    """10 态均有映射（终态为空集 = 无出边）。"""
    assert set(TRANSITIONS) == set(S)
    for terminal in (S.done, S.discarded, S.error):
        assert TRANSITIONS[terminal] == frozenset()


# ---------------- advance_to（批改判卷管线推进） ----------------


def test_advance_to_moves_forward_and_increments_version_by_steps():
    """沿主链推进：uploaded → grading 跨 3 步，版本 +3。"""
    status, version = advance_to(S.uploaded, S.grading, 0)
    assert status == S.grading
    assert version == 3


def test_advance_to_noop_when_already_past_target():
    """已不落后于目标：grading → previewing 原样返回（幂等）。"""
    status, version = advance_to(S.grading, S.previewing, 5)
    assert status == S.grading
    assert version == 5


def test_advance_to_noop_on_same_state():
    status, version = advance_to(S.done, S.done, 6)
    assert status == S.done
    assert version == 6


@pytest.mark.parametrize("terminal", [S.done, S.discarded, S.error])
def test_advance_to_noop_on_terminal(terminal):
    """终态不推进（advance_to 幂等，不抛异常）。"""
    status, version = advance_to(terminal, S.grading, 4)
    assert status == terminal
    assert version == 4


def test_advance_to_noop_off_grading_chain():
    """导入分支不在主链：importing 不推进。"""
    status, version = advance_to(S.importing, S.done, 2)
    assert status == S.importing
    assert version == 2


def test_advance_to_full_chain_to_done():
    status, version = advance_to(S.uploaded, S.done, 0)
    assert status == S.done
    assert version == 5
