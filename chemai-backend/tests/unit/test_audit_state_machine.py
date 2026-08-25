"""审核状态机测试（设计 D5，task 4.1/4.2）。

- 4.1 状态机纯函数全路径：passed→approve 入库；warning→approve 带复核标记 / 打回重生成；
  blocked→自动重生成 ≤3 次→标记出题失败。
- 4.2 自动重生成循环集成：3 次失败路径 + 重生成成功后放行。
"""
import pytest

from app.services.audit.equation import audit_equation
from app.services.audit.review import QuestionReviewResult, ReviewScores
from app.services.audit.state_machine import (
    APPROVED,
    GENERATION_FAILED,
    IN_REVIEW,
    MAX_REGENERATION_ATTEMPTS,
    REGENERATING,
    AuditState,
    advance_after_review,
    apply_action,
    run_audit_cycle,
)


def _review(status: str = "passed") -> QuestionReviewResult:
    return QuestionReviewResult(
        scores=ReviewScores(scientificity=90, difficulty=80, knowledge=85, discrimination=75),
        composite=84.25,
        status=status,
        reason=status,
    )


# ---- 4.1 状态机纯函数全路径 ----


def test_approve_passed_enters_bank():
    state = apply_action(AuditState(overall_status="passed"), "approve")
    assert state.status == APPROVED
    assert state.review_flag is False


def test_approve_warning_enters_bank_with_review_flag():
    state = apply_action(AuditState(overall_status="warning"), "approve")
    assert state.status == APPROVED
    assert state.review_flag is True


def test_approve_blocked_rejected():
    with pytest.raises(ValueError):
        apply_action(AuditState(overall_status="blocked"), "approve")


def test_regenerate_passed_rejected():
    with pytest.raises(ValueError):
        apply_action(AuditState(overall_status="passed"), "regenerate")


def test_regenerate_warning_returns_to_review():
    state = apply_action(AuditState(overall_status="warning"), "regenerate")
    assert state.status == REGENERATING
    assert state.regeneration_attempts == 1


def test_regenerate_exhausted_marks_failed():
    state = AuditState(overall_status="blocked", regeneration_attempts=MAX_REGENERATION_ATTEMPTS)
    state = apply_action(state, "regenerate")
    assert state.status == GENERATION_FAILED
    assert state.generation_failed is True


def test_unknown_action_raises():
    with pytest.raises(ValueError):
        apply_action(AuditState(overall_status="passed"), "bogus")


def test_advance_after_review_passed_waits_approval():
    state = advance_after_review(AuditState(overall_status="passed"), "passed")
    assert state.status == IN_REVIEW
    assert state.regeneration_attempts == 0


def test_advance_after_review_warning_waits_approval():
    state = advance_after_review(AuditState(overall_status="passed"), "warning")
    assert state.status == IN_REVIEW


def test_advance_after_review_blocked_triggers_regeneration():
    state = advance_after_review(AuditState(overall_status="passed"), "blocked")
    assert state.status == REGENERATING
    assert state.regeneration_attempts == 1


def test_meta_serialization():
    meta = AuditState(
        overall_status="warning", status=APPROVED, review_flag=True
    ).to_meta()
    assert meta == {"regeneration_attempts": 0, "generation_failed": False, "review_flag": True}


# ---- 4.2 自动重生成循环集成 ----


def test_cycle_blocked_three_times_marks_failed():
    """blocked 自动重生成至多 3 次，3 次仍 blocked → 标记出题失败。"""
    calls: list[str] = []

    def generate():
        calls.append("gen")
        return audit_equation("H2 + O2 → H2O"), _review()  # 方程式级 blocked

    state, reports = run_audit_cycle(generate)
    assert state.status == GENERATION_FAILED
    assert state.generation_failed is True
    assert state.regeneration_attempts == MAX_REGENERATION_ATTEMPTS
    # 初次审核 + 3 次重生成 = 4 次调用
    assert len(calls) == MAX_REGENERATION_ATTEMPTS + 1
    assert len(reports) == MAX_REGENERATION_ATTEMPTS + 1


def test_cycle_recovers_after_regeneration():
    """重生成后通过 → 放行等待教师批准，attempts 记录已发生的重生成次数。"""
    order = ["blocked", "passed"]
    reports_seen: list[str] = []

    def generate():
        kind = order.pop(0)
        if kind == "blocked":
            return audit_equation("H2 + O2 → H2O"), _review()
        reports_seen.append("passed")
        return audit_equation("2H2 + O2 → 2H2O"), _review()

    state, _ = run_audit_cycle(generate)
    assert state.status == IN_REVIEW
    assert state.overall_status == "passed"
    assert state.regeneration_attempts == 1


def test_cycle_passed_first_try_no_regeneration():
    def generate():
        return audit_equation("2H2 + O2 → 2H2O"), _review()

    state, reports = run_audit_cycle(generate)
    assert state.status == IN_REVIEW
    assert state.overall_status == "passed"
    assert state.regeneration_attempts == 0
    assert len(reports) == 1


def test_cycle_equation_only_manual_flow():
    """手动/OCR 只过方程式级（题目级 None）：blocked 依旧自动重生成。"""
    calls = []

    def generate():
        calls.append(1)
        return audit_equation("H2 + O2 → H2O"), None

    state, _ = run_audit_cycle(generate)
    assert state.status == GENERATION_FAILED
    assert len(calls) == MAX_REGENERATION_ATTEMPTS + 1
