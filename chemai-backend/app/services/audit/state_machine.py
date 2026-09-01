"""审核状态机（设计 D5，doc 26 审核状态机）。

状态机为纯函数：(overall_status, action) → next_state。
- passed → 教师 approve → 入库（status=approved）；
- warning → 教师 approve 放行带复核标记（review_flag=true）或打回重生成（regenerate）；
- blocked → 自动重生成 ≤3 次（regeneration_attempts 递增，每次重新两层审核）；
  3 次仍 blocked → 标记出题失败（generation_failed=true，不自动替换题库）。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from app.services.audit.equation import EquationAuditReport
from app.services.audit.review import QuestionReviewResult
from app.services.audit.workflow import compose_overall_status

MAX_REGENERATION_ATTEMPTS = 3

# 状态机状态
IN_REVIEW = "in_review"          # 待教师批准
APPROVED = "approved"            # 已入库
REGENERATING = "regenerating"    # 重生成中
GENERATION_FAILED = "generation_failed"  # 出题失败


@dataclass
class AuditState:
    """审核状态机状态快照。"""

    overall_status: str  # passed / warning / blocked
    status: str = IN_REVIEW
    regeneration_attempts: int = 0
    generation_failed: bool = False
    review_flag: bool = False

    def to_meta(self) -> dict:
        return {
            "regeneration_attempts": self.regeneration_attempts,
            "generation_failed": self.generation_failed,
            "review_flag": self.review_flag,
        }


def apply_action(state: AuditState, action: str) -> AuditState:
    """状态转移纯函数：approve 入库（warning 带复核标记）/ regenerate 重生成。"""
    if action == "approve":
        if state.overall_status == "blocked":
            raise ValueError("blocked 题目不可批准，应自动重生成")
        return replace(
            state,
            status=APPROVED,
            review_flag=state.review_flag or state.overall_status == "warning",
        )
    if action == "regenerate":
        if state.overall_status == "passed":
            raise ValueError("passed 题目无需重生成")
        if state.regeneration_attempts >= MAX_REGENERATION_ATTEMPTS:
            return replace(
                state,
                status=GENERATION_FAILED,
                generation_failed=True,
            )
        return replace(
            state,
            status=REGENERATING,
            regeneration_attempts=state.regeneration_attempts + 1,
        )
    raise ValueError(f"未知动作: {action}")


def advance_after_review(state: AuditState, overall_status: str) -> AuditState:
    """一次两层审核完成后推进状态机（纯函数）。

    blocked → 自动重生成（attempts 递增，达上限置出题失败）；
    passed / warning → 待教师批准（in_review），不自动重生成。
    """
    current = replace(
        state,
        overall_status=overall_status,
        status=IN_REVIEW,
    )
    if overall_status == "blocked":
        return apply_action(current, "regenerate")
    return current


def run_audit_cycle(
    generate: Callable[[], tuple[EquationAuditReport | None, QuestionReviewResult | None]],
    initial_state: AuditState | None = None,
) -> tuple[AuditState, list[tuple[EquationAuditReport | None, QuestionReviewResult | None]]]:
    """自动重生成循环（4.2）：每次重新两层审核，blocked 自动重生成 ≤3 次。

    generate() 执行一次两层审核（方程式级 + 题目级），返回 (equation_report, question_review)。
    题面无方程式时 equation_report 为 None，按方程式级 passed 合成（与 build_audit_report 一致）。
    返回 (最终状态, 历次审核报告)。
    """
    state = initial_state or AuditState(overall_status="passed")
    reports: list[tuple[EquationAuditReport | None, QuestionReviewResult | None]] = []
    while True:
        eq_report, question_review = generate()
        reports.append((eq_report, question_review))
        question_status = question_review.status if question_review is not None else None
        equation_status = eq_report.overall_status if eq_report is not None else "passed"
        overall = compose_overall_status(equation_status, question_status)
        state = advance_after_review(state, overall)
        if state.status != REGENERATING:
            break
    return state, reports
