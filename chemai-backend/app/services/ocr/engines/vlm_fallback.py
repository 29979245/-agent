"""VLM 兜底引擎（图片/PDF 最后降级层）。

复用现有 LLM fallback 通道（langchain-openai 三级回退，零新增依赖）：以文本 prompt
引导模型输出结构化 JSON（学号/姓名/逐题答案）。识别成功标记 fallback_used=True（降级
结果前端可见）；解析失败返回部分结果，不抛未捕获异常。
"""
from __future__ import annotations

import asyncio
import json

from app.services.diagnosis.llm_diagnosis import (
    FallbackDiagnosisLLMClient,
    extract_json,
)
from app.services.ocr.engines.base import OCRDocument, OCRResult, OCREngineError

VLM_SYSTEM_PROMPT = (
    "你是 OCR 识别助手。从答题卡图像中提取学号、姓名与逐题答案。"
    "学号为 8-11 位数字，无法识别用 unknown；姓名无法识别用 待识别；"
    "无法确定的题目跳过不输出。仅输出一个 JSON 对象，不要额外文字："
    '{"student_no": "", "student_name": "", "answers": [{"question_no": 1, "answer": "A"}]}'
)


class VLMError(OCREngineError):
    """VLM 识别结果无效（无 JSON / 解析失败）。"""


def build_vlm_prompt(path: str) -> list[dict]:
    return [
        {"role": "system", "content": VLM_SYSTEM_PROMPT},
        {"role": "user", "content": f"答题卡图片路径：{path}（图像内容以视觉模型能力为准）"},
    ]


def parse_vlm_result(text: str) -> OCRResult:
    """解析 VLM JSON 输出。无效响应抛 VLMError（由引擎捕获标记部分结果）。"""
    raw = extract_json(text or "")
    if raw is None:
        raise VLMError("VLM 响应中未找到 JSON")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise VLMError(f"VLM JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise VLMError("VLM JSON 顶层非对象")

    answers_raw = data.get("answers") or []
    answers: list[dict] = []
    for item in answers_raw:
        if isinstance(item, dict) and item.get("question_no") is not None:
            answers.append({"question_no": int(item["question_no"]), "answer": str(item.get("answer", ""))})
    return OCRResult(
        student_no=str(data.get("student_no") or "unknown"),
        student_name=str(data.get("student_name") or "待识别"),
        answers=answers,
        provider="vlm",
        confidence=0.7,
        degraded=True,
        fallback_used=True,
    )


class VLMFallbackEngine:
    """VLM 兜底引擎。client 可注入 Mock（协议：complete(messages) -> str）。"""

    name = "vlm"

    def __init__(self, client=None) -> None:
        self._client = client or FallbackDiagnosisLLMClient()

    async def extract(self, document: OCRDocument) -> OCRResult:
        messages = build_vlm_prompt(document.path)
        try:
            raw = await asyncio.to_thread(self._client.complete, messages)
            return parse_vlm_result(raw)
        except Exception as e:  # 兜底层：任何失败都返回部分结果，不中断流程
            return OCRResult(
                provider=self.name,
                partial=True,
                degraded=True,
                fallback_used=True,
                error=f"VLM 识别失败: {e}",
            )
