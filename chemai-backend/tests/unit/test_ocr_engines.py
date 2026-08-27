"""OCR 引擎单元测试（task 4.x）。

4.1 协议与文件类型判定 / 4.2 baidu_engine / 4.3 vlm_fallback / 4.4 mineru 可用性
/ 4.5 双引擎路由与降级链。无真实凭据：httpx 走 MockTransport、LLM 走注入 Mock。
"""
import json

import httpx
import pytest

from app.config import settings
from app.services.ocr.engines import (
    BaiduOCREngine,
    MinerUEngine,
    MinerUUnavailableError,
    OCRDocument,
    OCRProvider,
    OCRResult,
    VLMFallbackEngine,
    check_available,
    classify_document,
    extract_document,
)
from app.services.ocr.token import reset_token_cache

API_KEY = "ak_engine"
SECRET = "sk_engine"

FULL_WORDS = [
    "2023-2024 学年化学期中考试",
    "姓名：张三",
    "学号：2023001234",
    "1 A",
    "2 B",
    "3 C",
]


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setattr(settings, "baidu_ocr_api_key", API_KEY)
    monkeypatch.setattr(settings, "baidu_ocr_secret_key", SECRET)
    reset_token_cache()
    yield
    reset_token_cache()


def _baidu_client(words, error_code=None):
    def handler(request: httpx.Request):
        if "/oauth/2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tk", "expires_in": 2592000})
        if error_code:
            return httpx.Response(200, json={"error_code": error_code, "error_msg": "mock error"})
        return httpx.Response(200, json={"words_result": [{"words": w} for w in words]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://aip.baidubce.com")


def _failing_baidu_client(status=500):
    def handler(request: httpx.Request):
        if "/oauth/2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tk", "expires_in": 2592000})
        return httpx.Response(status, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://aip.baidubce.com")


class _FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return self.text


def _doc(tmp_path, name, content=b"fake-bytes"):
    p = tmp_path / name
    p.write_bytes(content)
    return OCRDocument(path=str(p))


# ---- 4.1 协议与文件类型判定 ----


def test_classify_document_image_vs_pdf():
    assert classify_document(OCRDocument(path="a.png")) == "image"
    assert classify_document(OCRDocument(path="a.jpg")) == "image"
    assert classify_document(OCRDocument(path="a.jpeg")) == "image"
    assert classify_document(OCRDocument(path="a.pdf")) == "pdf"


def test_ocr_provider_protocol_structural():
    """协议类型标注可被路由引用：extract(document) → OCRResult。"""
    assert hasattr(OCRProvider, "extract")
    assert hasattr(OCRResult, "student_no")


# ---- 4.2 baidu_engine ----


@pytest.mark.asyncio
async def test_baidu_extract_full(tmp_path):
    async with _baidu_client(FULL_WORDS) as client:
        engine = BaiduOCREngine(http=client)
        result = await engine.extract(_doc(tmp_path, "answer.png"))
    assert result.provider == "baidu"
    assert result.student_no == "2023001234"
    assert result.student_name == "张三"
    assert result.answers == [
        {"question_no": 1, "answer": "A"},
        {"question_no": 2, "answer": "B"},
        {"question_no": 3, "answer": "C"},
    ]
    assert result.partial is False
    assert result.fallback_used is False


@pytest.mark.asyncio
async def test_baidu_extract_partial_when_text_too_short(tmp_path):
    async with _baidu_client(["", "   "]) as client:
        engine = BaiduOCREngine(http=client)
        result = await engine.extract(_doc(tmp_path, "blank.png"))
    assert result.partial is True
    assert result.degraded is True
    assert result.student_no == "unknown"
    assert "未识别" in result.error


@pytest.mark.asyncio
async def test_baidu_extract_unknown_student_info_keeps_answers(tmp_path):
    async with _baidu_client(["1 A", "2 B", "11 C"]) as client:
        engine = BaiduOCREngine(http=client)
        result = await engine.extract(_doc(tmp_path, "x.png"))
    assert result.student_no == "unknown"
    assert result.student_name == "待识别"
    assert result.answers == [
        {"question_no": 1, "answer": "A"},
        {"question_no": 2, "answer": "B"},
        {"question_no": 11, "answer": "C"},
    ]


@pytest.mark.asyncio
async def test_baidu_http_error_raises_engine_error(tmp_path):
    async with _failing_baidu_client() as client:
        engine = BaiduOCREngine(http=client)
        with pytest.raises(Exception):
            await engine.extract(_doc(tmp_path, "boom.png"))


# ---- 4.3 vlm_fallback ----


@pytest.mark.asyncio
async def test_vlm_extract_success(tmp_path):
    text = json.dumps(
        {"student_no": "2023005678", "student_name": "李四",
         "answers": [{"question_no": 1, "answer": "B"}]}
    )
    llm = _FakeLLM(text)
    engine = VLMFallbackEngine(client=llm)
    result = await engine.extract(_doc(tmp_path, "v.png"))
    assert result.provider == "vlm"
    assert result.fallback_used is True
    assert result.degraded is True
    assert result.student_no == "2023005678"
    assert result.student_name == "李四"
    assert result.answers == [{"question_no": 1, "answer": "B"}]
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_vlm_extract_unparseable_marks_partial(tmp_path):
    llm = _FakeLLM("这张图我认不出来")
    engine = VLMFallbackEngine(client=llm)
    result = await engine.extract(_doc(tmp_path, "v.png"))
    assert result.partial is True
    assert result.fallback_used is True
    assert result.error


# ---- 4.4 mineru_engine 可用性检测 ----


def test_mineru_check_available_false_when_not_installed():
    assert check_available() is False


@pytest.mark.asyncio
async def test_mineru_extract_raises_when_unavailable(tmp_path):
    engine = MinerUEngine()
    with pytest.raises(MinerUUnavailableError):
        await engine.extract(_doc(tmp_path, "a.pdf"))


# ---- 4.5 双引擎路由与降级链 ----


@pytest.mark.asyncio
async def test_router_image_baidu_success_short_circuit(tmp_path):
    """图片：百度成功即返回，不进 VLM（成功短路）。"""
    async with _baidu_client(FULL_WORDS) as client:
        llm = _FakeLLM('{"student_no":"x","student_name":"x","answers":[]}')
        result = await extract_document(_doc(tmp_path, "ans.png"), http=client, llm_client=llm)
    assert result.provider == "baidu"
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_router_image_baidu_fail_falls_to_vlm(tmp_path):
    """图片：百度失败 → VLM 兜底。"""
    async with _failing_baidu_client() as client:
        llm = _FakeLLM('{"student_no":"2023005678","student_name":"李四","answers":[]}')
        result = await extract_document(_doc(tmp_path, "ans.png"), http=client, llm_client=llm)
    assert result.provider == "vlm"
    assert result.fallback_used is True
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_router_image_all_fail_returns_partial(tmp_path):
    """图片：百度 + VLM 均失败 → 部分结果，不抛未捕获异常。"""
    async with _failing_baidu_client() as client:
        llm = _FakeLLM("认不出")
        result = await extract_document(_doc(tmp_path, "ans.png"), http=client, llm_client=llm)
    assert result.partial is True
    assert result.degraded is True


@pytest.mark.asyncio
async def test_router_pdf_mineru_unavailable_falls_to_baidu(tmp_path):
    """PDF：MinerU 不可用 → 百度成功即返回。"""
    async with _baidu_client(FULL_WORDS) as client:
        llm = _FakeLLM('{"student_no":"x","student_name":"x","answers":[]}')
        result = await extract_document(_doc(tmp_path, "sheet.pdf"), http=client, llm_client=llm)
    assert result.provider == "baidu"
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_router_pdf_all_fail_returns_partial(tmp_path):
    """PDF：MinerU→百度→VLM 全失败 → 部分结果。"""
    async with _failing_baidu_client() as client:
        llm = _FakeLLM("认不出")
        result = await extract_document(_doc(tmp_path, "sheet.pdf"), http=client, llm_client=llm)
    assert result.partial is True
    assert result.degraded is True


@pytest.mark.asyncio
async def test_router_baidu_bad_json_still_degrades_to_vlm(tmp_path):
    """百度返回 200 但响应体非 JSON（代理错误页）→ JSONDecodeError 不打断降级链。"""

    def handler(request: httpx.Request):
        if "/oauth/2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tk", "expires_in": 2592000})
        return httpx.Response(200, text="<html>proxy error</html>")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://aip.baidubce.com"
    ) as client:
        llm = _FakeLLM('{"student_no":"2023005678","student_name":"李四","answers":[]}')
        result = await extract_document(_doc(tmp_path, "ans.png"), http=client, llm_client=llm)
    assert result.provider == "vlm"
    assert llm.calls == 1
