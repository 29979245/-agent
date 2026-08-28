"""UploadSession 状态机：10 态流转 + 终态守卫 + 乐观锁版本递增（纯函数）。

设计 D1：状态机为纯函数，便于终态守卫与非法迁移独立 TDD 测试。
- uploaded → previewing → ready → (importing→imported | grading→graded) → done；
- discarded / error 为终态（服务侧直接置位，本机不出边）。
"""
from __future__ import annotations

from app.db.models.enums import UploadSessionStatus

# 终态：对已处于终态的会话做任何状态变更均抛异常
TERMINAL = frozenset(
    {
        UploadSessionStatus.done,
        UploadSessionStatus.discarded,
        UploadSessionStatus.error,
    }
)

# 批改判卷主链（uploaded → previewing → ready → grading → graded → done）：
# advance_to 沿此链推进，importing/imported 为题库导入分支，不在链上。
GRADING_CHAIN = [
    UploadSessionStatus.uploaded,
    UploadSessionStatus.previewing,
    UploadSessionStatus.ready,
    UploadSessionStatus.grading,
    UploadSessionStatus.graded,
    UploadSessionStatus.done,
]

# 合法迁移表：每个状态的可达目标集合（终态为空集 = 无出边）
TRANSITIONS: dict[UploadSessionStatus, frozenset[UploadSessionStatus]] = {
    UploadSessionStatus.uploaded: frozenset({UploadSessionStatus.previewing}),
    UploadSessionStatus.previewing: frozenset({UploadSessionStatus.ready}),
    UploadSessionStatus.ready: frozenset(
        {UploadSessionStatus.importing, UploadSessionStatus.grading}
    ),
    UploadSessionStatus.importing: frozenset({UploadSessionStatus.imported}),
    UploadSessionStatus.imported: frozenset({UploadSessionStatus.done}),
    UploadSessionStatus.grading: frozenset({UploadSessionStatus.graded}),
    UploadSessionStatus.graded: frozenset({UploadSessionStatus.done}),
    UploadSessionStatus.done: frozenset(),
    UploadSessionStatus.discarded: frozenset(),
    UploadSessionStatus.error: frozenset(),
}


class SessionStateMachineError(ValueError):
    """状态机违规：终态变更守卫或非法迁移。"""


def transfer(
    session_status: UploadSessionStatus,
    target: UploadSessionStatus,
    version: int = 0,
) -> tuple[UploadSessionStatus, int]:
    """执行一次状态迁移。

    - 终态守卫：当前为 done/discarded/error 时任何变更抛 SessionStateMachineError；
    - 非法迁移：目标不在当前状态可达集合时抛 SessionStateMachineError；
    - 成功：返回 (目标状态, version+1)。
    """
    if session_status in TERMINAL:
        raise SessionStateMachineError(
            f"终态 {session_status.value} 不可再做状态变更"
        )
    allowed = TRANSITIONS.get(session_status, frozenset())
    if target not in allowed:
        raise SessionStateMachineError(
            f"非法状态迁移: {session_status.value} → {target.value}"
        )
    return target, version + 1


def advance_to(
    session_status: UploadSessionStatus,
    target: UploadSessionStatus,
    version: int = 0,
) -> tuple[UploadSessionStatus, int]:
    """沿批改判卷主链推进到目标状态（服务端点用，幂等）。

    - 当前状态不在主链（终态/导入分支 importing/imported）或已不落后于目标 → 原样返回；
    - 否则返回 (target, version + 跨步数)。
    """
    if session_status in TERMINAL or session_status not in GRADING_CHAIN:
        return session_status, version
    src = GRADING_CHAIN.index(session_status)
    dst = GRADING_CHAIN.index(target) if target in GRADING_CHAIN else src
    if dst <= src:
        return session_status, version
    return target, version + (dst - src)
