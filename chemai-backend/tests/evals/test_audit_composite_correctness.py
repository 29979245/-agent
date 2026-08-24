"""双层合成正确率评测（task 3.3 / 设计 D3，≥90%）。

固定 mock LLM 分数输入 + 两层状态交叉，只测合成纯函数（compose_overall_status +
classify_review），不测 LLM 本身。作为 run_evals --tier l1/l2 的合成正确率门禁。
"""
from app.evals.cases import CLASSIFY_CASES, COMPOSE_CASES, composite_correctness
from app.services.audit.review import classify_review
from app.services.audit.workflow import compose_overall_status


def _correct_rate(cases, fn) -> float:
    wrong = sum(1 for *args, expected in cases if fn(*args) != expected)
    return 1 - wrong / len(cases)


def test_compose_overall_status_correctness():
    rate = _correct_rate(COMPOSE_CASES, compose_overall_status)
    assert rate >= 0.9, f"双层合成正确率 {rate:.0%} < 90%"


def test_classify_review_correctness():
    rate = _correct_rate(CLASSIFY_CASES, lambda c, s: classify_review(c, s)[0])
    assert rate >= 0.9, f"阈值判定正确率 {rate:.0%} < 90%"


def test_shared_runner_same_correctness():
    """与 run_evals 共用同一用例源：合成正确率 ≥90%。"""
    rates = composite_correctness()
    assert all(rate >= 0.9 for rate in rates.values())
