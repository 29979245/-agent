"""题目级四维评审服务测试（doc 26 §9.4）。

覆盖：固定权重综合分、阈值判定边界、科学性 <70 硬阻断、LLM 响应解析、
Mock 客户端评审调用、Fallback 链失败路径。
"""
import pytest
from app.agents.factories.model_factory import ProviderError

from app.config import settings
from app.services.audit.review import (
    PROVIDER_CHAIN,
    RETRY_PER_PROVIDER,
    FallbackReviewLLMClient,
    ReviewLLMError,
    ReviewScores,
    classify_review,
    composite_score,
    parse_review_response,
    review_question,
)


class MockReviewClient:
    """可注入评审客户端：按序返回预设文本，记录调用消息。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


# ---- 固定权重综合分（doc 26：0.4/0.25/0.2/0.15）----


def test_composite_score_doc_example():
    # 科学性90 / 难度80 / 知识点85 / 区分度75 → 84.25（spec 示例）
    assert composite_score(90, 80, 85, 75) == pytest.approx(84.25)


def test_composite_score_all_100():
    assert composite_score(100, 100, 100, 100) == pytest.approx(100.0)


def test_composite_score_zero():
    assert composite_score(0, 0, 0, 0) == pytest.approx(0.0)


# ---- 阈值判定与边界 ----


def test_classify_passed_high():
    status, _ = classify_review(84.25, 90)
    assert status == "passed"


def test_classify_warning_mid():
    status, _ = classify_review(75.0, 85)
    assert status == "warning"


def test_classify_blocked_low_composite():
    status, _ = classify_review(65.0, 80)
    assert status == "blocked"


def test_classify_blocked_scientificity_red_line():
    # 科学性 65 <70 → 硬阻断，即使综合分高
    status, reason = classify_review(90.0, 65)
    assert status == "blocked"
    assert "科学性" in reason


def test_classify_boundary_80():
    assert classify_review(80.0, 80)[0] == "passed"
    assert classify_review(79.99, 80)[0] == "warning"


def test_classify_boundary_70():
    assert classify_review(70.0, 80)[0] == "warning"
    assert classify_review(69.99, 80)[0] == "blocked"


def test_classify_scientificity_boundary_70_not_blocked():
    # 科学性恰好 70 不触发硬阻断，按综合分判定
    assert classify_review(85.0, 70)[0] == "passed"


# ---- LLM 响应解析 ----


def test_parse_review_response_plain_json():
    scores = parse_review_response(
        '{"scientificity": 90, "difficulty": 80, "knowledge": 85, "discrimination": 75}'
    )
    assert scores.scientificity == 90
    assert scores.difficulty == 80
    assert scores.knowledge == 85
    assert scores.discrimination == 75


def test_parse_review_response_markdown_fence():
    text = '```json\n{"scientificity": 95, "difficulty": 70, "knowledge": 80, "discrimination": 70}\n```'
    scores = parse_review_response(text)
    assert scores.scientificity == 95


def test_parse_review_response_with_evidence_and_noise():
    text = "好的，评审如下：\n" '{"scientificity": 88, "difficulty": 72, "knowledge": 90, "discrimination": 78, "evidence": {"scientificity": "化学式正确", "difficulty": "难度适中"}}'
    scores = parse_review_response(text)
    assert scores.evidence["scientificity"] == "化学式正确"
    assert scores.evidence["knowledge"] == ""


def test_parse_review_response_string_scores():
    scores = parse_review_response(
        '{"scientificity": "90", "difficulty": "80", "knowledge": "85", "discrimination": "75"}'
    )
    assert scores.scientificity == 90.0


def test_parse_review_response_clamps_out_of_range():
    scores = parse_review_response(
        '{"scientificity": 120, "difficulty": -5, "knowledge": 85, "discrimination": 75}'
    )
    assert scores.scientificity == 100.0
    assert scores.difficulty == 0.0


def test_parse_review_response_empty_raises():
    with pytest.raises(ReviewLLMError):
        parse_review_response("")


def test_parse_review_response_no_json_raises():
    with pytest.raises(ReviewLLMError):
        parse_review_response("抱歉，无法评审")


# ---- review_question（Mock 客户端）----


GOOD_QUESTION = {
    "content": "在密闭容器中加热高锰酸钾，观察到的现象是？",
    "options": ["A 有气泡产生", "B 固体颜色变浅"],
    "answer": "B",
    "analysis": "2KMnO4 加热分解产生 O2，固体质量减少颜色变浅。",
    "knowledge_points": "高锰酸钾分解",
    "difficulty": "medium",
}


def test_review_question_passes_with_mock():
    client = MockReviewClient([
        '{"scientificity": 90, "difficulty": 80, "knowledge": 85, "discrimination": 75}'
    ])
    result = review_question(GOOD_QUESTION, client)
    assert result.status == "passed"
    assert result.composite == pytest.approx(84.25)
    assert result.scores.scientificity == 90
    assert client.calls, "应调用 LLM 客户端"


def test_review_question_prompt_includes_question_fields():
    client = MockReviewClient([
        '{"scientificity": 90, "difficulty": 80, "knowledge": 85, "discrimination": 75}'
    ])
    review_question(GOOD_QUESTION, client)
    user_content = client.calls[0][1]["content"]
    assert "高锰酸钾" in user_content
    assert "medium" in user_content
    assert "2KMnO4" in user_content


def test_review_question_scientificity_hard_block():
    client = MockReviewClient([
        '{"scientificity": 65, "difficulty": 100, "knowledge": 100, "discrimination": 100}'
    ])
    result = review_question(GOOD_QUESTION, client)
    assert result.status == "blocked"
    assert result.composite > 80  # 综合分高但被科学性红线阻断


def test_review_question_warning_status():
    client = MockReviewClient([
        '{"scientificity": 80, "difficulty": 70, "knowledge": 75, "discrimination": 70}'
    ])
    result = review_question(GOOD_QUESTION, client)
    assert result.status == "warning"


def test_review_question_blocked_low_composite():
    client = MockReviewClient([
        '{"scientificity": 75, "difficulty": 50, "knowledge": 55, "discrimination": 50}'
    ])
    result = review_question(GOOD_QUESTION, client)
    assert result.status == "blocked"


def test_review_question_empty_response_raises():
    client = MockReviewClient([""])
    with pytest.raises(ReviewLLMError):
        review_question(GOOD_QUESTION, client)


# ---- Fallback 链 ----


def test_fallback_client_delegates_to_llmclient(monkeypatch):
    # 委托 model_factory.LLMClient.complete_chain（真 HTTP + 熔断/回退）
    calls = {"n": 0}

    class RecordingLLM:
        def __init__(self, *args, **kwargs):
            pass

        def complete_chain(self, messages):
            calls["n"] += 1
            return '{"scientificity": 90, "difficulty": 80, "knowledge": 85, "discrimination": 75}'

    monkeypatch.setattr("app.services.audit.review.LLMClient", RecordingLLM)
    client = FallbackReviewLLMClient()
    text = client.complete([{"role": "user", "content": "hi"}])
    assert "scientificity" in text
    assert calls["n"] == 1


def test_fallback_client_wraps_provider_error(monkeypatch):
    class FailingLLM:
        def __init__(self, *args, **kwargs):
            pass

        def complete_chain(self, messages):
            raise ProviderError("deepseek", "连接超时", status_code=503)

    monkeypatch.setattr("app.services.audit.review.LLMClient", FailingLLM)
    client = FallbackReviewLLMClient()
    with pytest.raises(ReviewLLMError, match="deepseek"):
        client.complete([{"role": "user", "content": "hi"}])


def test_fallback_client_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    assert FallbackReviewLLMClient().available is False


def test_fallback_client_available_with_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    assert FallbackReviewLLMClient().available is True


def test_fallback_provider_chain_definition():
    assert PROVIDER_CHAIN == ("mimo", "qwen", "deepseek")
    assert RETRY_PER_PROVIDER == 3
