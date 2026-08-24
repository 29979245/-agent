"""题目级评审科学准确性评测（task 2.3 / 6.2，L3 层）。

Golden 子集（专家标注科学分）对比评审模型科学分，|误差| ≤ ±15 视为准确，
准确率 ≥75% 通过。未配置 llm_api_key 时 SKIP（不阻断 CI）。
"""
import pytest

from app.config import settings
from app.evals.runners.run_evals import L3_PASS_RATE, _load_golden, GOLDEN_PATH
from app.services.audit.review import FallbackReviewLLMClient, ReviewLLMError, review_question

pytestmark = pytest.mark.skipif(
    not settings.llm_api_key,
    reason="未配置 llm_api_key，L3 评测需真实 LLM",
)


def test_review_scientificity_accuracy():
    golden = _load_golden(GOLDEN_PATH)
    assert golden, "Golden 数据集不应为空"
    client = FallbackReviewLLMClient()
    matches = 0
    evaluated = 0
    for case in golden:
        try:
            result = review_question(case, client)
        except ReviewLLMError:
            continue
        evaluated += 1
        if abs(result.scores.scientificity - case["scientificity"]) <= 15:
            matches += 1
    assert evaluated > 0, "无可用 LLM 评审结果"
    rate = matches / evaluated
    assert rate >= L3_PASS_RATE, f"科学准确性 {rate:.0%} < {L3_PASS_RATE:.0%}"
