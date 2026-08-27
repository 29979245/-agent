"""百度教育 OCR 引擎（主场景：图片答题卡）。

doc_analysis 同步调用 + 学号/姓名正则提取（8-11 位学号、unknown/待识别 兜底）；
未识别到有效文本（<10 字符）标记部分结果。凭据/HTTP 失败抛 BaiduOCRCallError
（路由捕获后降级 VLM）。
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import httpx

from app.services.ocr.engines.base import (
    MIN_OCR_TEXT_CHARS,
    OCRDocument,
    OCRResult,
    OCREngineError,
)
from app.services.ocr.token import BaiduTokenError, get_access_token

DOC_ANALYSIS_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/doc_analysis"

# 学号：带标签（学号：xxxx）优先，否则兜底裸 8-11 位数字
_STUDENT_NO_LABEL_RE = re.compile(r"学号\s*[:：]?\s*([0-9]{8,11})")
_STUDENT_NO_BARE_RE = re.compile(r"(?<!\d)([0-9]{8,11})(?!\d)")
_NAME_RE = re.compile(r"姓名\s*[:：]?\s*([一-龥]{2,4})")
# 逐题答案："1 A" / "1.A" / "1：B" / "2×"
_ANSWER_RE = re.compile(r"^\s*(\d{1,3})\s*[\.．、:：]?\s*([A-Ha-h]|[√×])\s*$")


class BaiduOCRCallError(OCREngineError):
    """百度 OCR 调用失败（凭据/传输/业务错误码）。"""


def _extract_student_info(lines: list[str]) -> tuple[str, str]:
    student_no, student_name = "unknown", "待识别"
    for line in lines:
        m = _STUDENT_NO_LABEL_RE.search(line)
        if m:
            student_no = m.group(1)
        m = _NAME_RE.search(line)
        if m:
            student_name = m.group(1)
    if student_no == "unknown":
        for line in lines:
            m = _STUDENT_NO_BARE_RE.search(line)
            if m:
                student_no = m.group(1)
                break
    return student_no, student_name


def _extract_answers(lines: list[str]) -> list[dict]:
    answers: list[dict] = []
    for line in lines:
        m = _ANSWER_RE.match(line)
        if m:
            answers.append({"question_no": int(m.group(1)), "answer": m.group(2).upper()})
    return answers


def _parse_words_result(data: dict) -> list[str]:
    words = data.get("words_result", []) if isinstance(data, dict) else []
    return [w.get("words", "").strip() for w in words if isinstance(w, dict) and w.get("words")]


class BaiduOCREngine:
    """百度教育 OCR 引擎。http 可注入（测试 MockTransport），None 时内部创建临时客户端。"""

    name = "baidu"

    def __init__(self, http: httpx.AsyncClient | None = None, now: float | None = None) -> None:
        self._http = http
        self._now = now

    async def extract(self, document: OCRDocument) -> OCRResult:
        try:
            token = await get_access_token(http=self._http, now=self._now)
            lines = await self._doc_analysis(token, document.path)
        # OSError（文件缺失）保持硬失败：调度器按 failed 记录供重试，不降级掩盖问题
        except (BaiduTokenError, httpx.HTTPError, json.JSONDecodeError) as e:
            raise BaiduOCRCallError(f"百度 OCR 调用失败: {e}") from e

        student_no, student_name = _extract_student_info(lines)
        answers = _extract_answers(lines)
        result = OCRResult(
            student_no=student_no,
            student_name=student_name,
            answers=answers,
            provider=self.name,
            confidence=0.9,
        )
        text = "".join(lines).replace(" ", "")
        if len(text) < MIN_OCR_TEXT_CHARS:
            result.partial = True
            result.degraded = True
            result.confidence = 0.0
            result.error = "未识别到有效文本"
        return result

    async def _doc_analysis(self, token: str, path: str) -> list[str]:
        image_b64 = base64.b64encode(Path(path).read_bytes()).decode()
        params = {"access_token": token}
        data = {"image": image_b64}
        if self._http is not None:
            resp = await self._http.post(DOC_ANALYSIS_URL, params=params, data=data)
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(DOC_ANALYSIS_URL, params=params, data=data)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("error_code"):
            raise BaiduOCRCallError(
                f"百度 OCR 业务错误码: {body.get('error_msg', body.get('error_code'))}"
            )
        return _parse_words_result(body)
