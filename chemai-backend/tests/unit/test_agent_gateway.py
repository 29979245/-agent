"""Gateway 意图分类单测（doc 30 六 / spec Gateway 意图分类，tasks 5.1）。

- 关键词兜底：navigate 规则 / chat 规则 / 默认 chat。
- LLM 语义分类优先：返回 JSON → llm 结果；失败降级关键词。
- 合并：LLM 无工具补关键词；LLM navigate 无 page 补关键词 page。
- 图片消息 → 视觉 Provider（MiMo 主位）。
- navigate 快捷路径：navigate → done → 结束帧（流终止）。
"""
import pytest

from app.agents.factories.model_factory import ProviderError
from app.agents.gateway import (
    PAGE_EXAM,
    PAGE_STUDENTS,
    IntentResult,
    _is_image_message,
    _merge,
    _parse_intent,
    classify_intent,
    keyword_classify,
    navigate_shortcut,
)


class _FakeLLM:
    def __init__(self, result=None, error=False, calls=None):
        self.result = result
        self.error = error
        self.calls = calls if calls is not None else []

    async def acomplete(self, provider, messages):
        self.calls.append(("acomplete", provider))
        if self.error:
            raise ProviderError(provider, "boom")
        return self.result

    async def acomplete_chain(self, messages):
        self.calls.append(("acomplete_chain", None))
        if self.error:
            raise ProviderError("all", "boom")
        return self.result


# ---------------------------------------------------------------- 关键词兜底

def test_keyword_navigate_exam():
    result = keyword_classify("请打开考试工作台")
    assert result.type == "navigate"
    assert result.page == PAGE_EXAM
    assert result.source == "keyword"


def test_keyword_navigate_students():
    result = keyword_classify("跳转到学生管理页")
    assert result.type == "navigate"
    assert result.page == PAGE_STUDENTS


def test_keyword_chat_question_generation():
    result = keyword_classify("帮我出几道氧化还原的题")
    assert result.type == "chat"
    assert "generate_questions" in result.tools


def test_keyword_chat_default():
    result = keyword_classify("你好，化学老师")
    assert result.type == "chat"
    assert result.tools == []


# ---------------------------------------------------------------- LLM 优先

def test_llm_result_priority():
    import asyncio
    llm = _FakeLLM(result='{"type": "chat", "tools": ["diagnose_barrier"], "page": ""}')
    r = asyncio.run(classify_intent("小明最近学得怎么样", llm=llm))
    assert r.type == "chat"
    assert r.tools == ["diagnose_barrier"]
    assert r.source == "llm"


def test_llm_failure_degrades_to_keyword():
    llm = _FakeLLM(error=True)
    import asyncio
    r = asyncio.run(classify_intent("帮我出几道题", llm=llm))
    assert r.source == "keyword"
    assert "generate_questions" in r.tools


def test_llm_unparseable_degrades_to_keyword():
    llm = _FakeLLM(result="抱歉，我无法理解")
    import asyncio
    r = asyncio.run(classify_intent("打开学生管理", llm=llm))
    assert r.source == "keyword"
    assert r.type == "navigate"
    assert r.page == PAGE_STUDENTS


def test_no_llm_uses_keyword_only():
    import asyncio
    r = asyncio.run(classify_intent("打开学生管理", llm=None))
    assert r.source == "keyword"
    assert r.type == "navigate"


# ---------------------------------------------------------------- 合并

def test_merge_chat_empty_llm_tools_uses_keyword():
    merged = _merge(IntentResult(type="chat", tools=[], source="llm"),
                    keyword_classify("帮我出几道题"))
    assert merged.type == "chat"
    assert "generate_questions" in merged.tools


def test_merge_navigate_missing_page_uses_keyword_page():
    merged = _merge(IntentResult(type="navigate", tools=[], page=None, source="llm"),
                    keyword_classify("打开学生管理"))
    assert merged.page == PAGE_STUDENTS


def test_merge_dedup_and_cap_three():
    merged = _merge(IntentResult(type="chat", tools=["a", "a", "b"], source="llm"),
                    IntentResult(type="chat", tools=["b", "c", "d"]))
    assert merged.tools == ["a", "b", "c"]


# ---------------------------------------------------------------- 解析

def test_parse_intent_code_fence_json():
    result = _parse_intent('```json\n{"type": "navigate", "tools": [], "page": "exam-v2"}\n```')
    assert result is not None
    assert result.type == "navigate"
    assert result.page == "exam-v2"


def test_parse_intent_invalid_returns_none():
    assert _parse_intent("not json") is None
    assert _parse_intent('{"type": "bogus"}') is None
    assert _parse_intent('{"tools": ["x"]}') is None  # 缺 type


# ---------------------------------------------------------------- 图片消息 / 快捷路径

def test_image_message_detection():
    assert _is_image_message("这张图片帮忙识别一下")
    assert _is_image_message("上传试卷照片")
    assert not _is_image_message("什么是氧化还原")


def test_image_message_uses_vision_provider():
    calls = []
    llm = _FakeLLM(result='{"type": "chat", "tools": []}', calls=calls)
    import asyncio
    asyncio.run(classify_intent("识别这张图片里的题目", llm=llm))
    assert ("acomplete", "mimo") in calls  # 视觉 Provider 优先


def test_navigate_shortcut_emits_navigate_then_done():
    import asyncio

    async def _collect():
        return [(name, payload) async for name, payload in navigate_shortcut(PAGE_EXAM, {"foo": "bar"})]

    frames = asyncio.run(_collect())
    names = [name for name, _ in frames]
    assert names == ["navigate", "done"]
    nav = dict(frames[0][1])
    assert nav["page"] == PAGE_EXAM
    assert nav["params"] == {"foo": "bar"}
