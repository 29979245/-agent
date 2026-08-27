"""题库确定性抽样（design.md D2）：按知识点 + 难度从真题库复制抽样，不依赖 LLM。

- 优先精确难度匹配，不足时用同知识点其他难度补足并标记抽样不足
- 排除调用方指定排除的题（ref_id，如刚做错的原题）
- 返回 (选中的 [ref_id, HistoricalQuestion] 列表, 抽样不足数量)
"""
from __future__ import annotations

from app.services.question.historical import HistoricalBank, HistoricalQuestion


def _ref_id(paper, hq: HistoricalQuestion) -> str:
    return f"{paper.region}/{paper.year}/{paper.name}#{hq.id}"


def _kp_hit(hq: HistoricalQuestion, knowledge_points: list[str]) -> bool:
    kp_set = [k for k in knowledge_points if k]
    if not kp_set:
        return True  # 无知识点目标时不过滤知识点
    return any(kp in hq.knowledge_points for kp in kp_set)


def sample_questions(
    bank: HistoricalBank,
    knowledge_points: list[str],
    difficulty: str,
    count: int = 3,
    exclude_ref_ids: tuple[str, ...] = (),
    choice_only: bool = False,
) -> tuple[list[tuple[str, HistoricalQuestion]], int]:
    """从真题库抽样 count 道：优先同难度，不足用同知识点其他难度补足。

    choice_only=True 时仅抽样带选项的选择题——学生答题界面按选项作答，
    开放题（无 options）抽入会导致生成不可作答的练习。
    返回 ([(ref_id, HistoricalQuestion)], shortfall)。shortfall = count - 实际数量。
    """
    exclude = set(exclude_ref_ids)
    exact: list[tuple[str, HistoricalQuestion]] = []
    fallback: list[tuple[str, HistoricalQuestion]] = []
    for paper in bank.papers:
        for hq in paper.questions:
            ref = _ref_id(paper, hq)
            if ref in exclude:
                continue
            if not _kp_hit(hq, knowledge_points):
                continue
            if choice_only and not hq.options:
                continue
            (exact if hq.difficulty == difficulty else fallback).append((ref, hq))
    exact.sort(key=lambda item: item[0])
    fallback.sort(key=lambda item: item[0])
    selected = list(exact[:count])
    if len(selected) < count:
        selected += fallback[: count - len(selected)]
    return selected, max(0, count - len(selected))
