"""题目级四维评审服务（LLM，doc 26 课程版四维评审）。

输入题目全量字段（正文/选项/答案/解析/知识点/难度）→ LLM 输出四维 0-100 评分 +
每维 evidence → 固定权重合成综合分（0.4/0.25/0.2/0.15）→ 阈值判定
（≥80 passed / 70-79 warning / <70 blocked；科学性单维 <70 硬阻断）。

LLM 调用经可注入客户端（ReviewLLMClient 协议）：生产默认三级 Fallback 链
MiMo-V2.5 → qwen-turbo → DeepSeek（每级 3 次重试 + 指数退避），测试注入 Mock 客户端。
"""
import json
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

from app.config import settings

# 四维权重（doc 26 / 设计 D2）：固定权重，作为合成与评测的单一真值来源
WEIGHTS = {
    "scientificity": 0.4,
    "difficulty": 0.25,
    "knowledge": 0.2,
    "discrimination": 0.15,
}

REVIEW_DIMENSIONS = ("scientificity", "difficulty", "knowledge", "discrimination")

# 判定阈值：综合分映射 + 科学性单维红线
PASS_THRESHOLD = 80.0
WARNING_THRESHOLD = 70.0
SCIENTIFICITY_RED_LINE = 70.0

# Fallback 链（与 config.py 注释一致：首选 MiMo-V2.5 → qwen-turbo → DeepSeek）
PROVIDER_CHAIN = ("mimo", "qwen", "deepseek")
RETRY_PER_PROVIDER = 3
RETRY_BASE_DELAY = 0.5  # 指数退避基数（秒）


class ReviewLLMError(RuntimeError):
    """LLM 评审调用失败（全部 Provider 重试耗尽 / 响应不可解析）。"""


class ReviewLLMClient(Protocol):
    """评审 LLM 客户端：complete(messages) -> 文本。实现可注入以便测试 Mock。"""

    def complete(self, messages: list[dict]) -> str: ...


@dataclass
class ReviewScores:
    """四维评分（0-100）与每维 evidence。"""

    scientificity: float = 0.0
    difficulty: float = 0.0
    knowledge: float = 0.0
    discrimination: float = 0.0
    evidence: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "scientificity": {"score": self.scientificity, "evidence": self.evidence.get("scientificity", "")},
            "difficulty": {"score": self.difficulty, "evidence": self.evidence.get("difficulty", "")},
            "knowledge": {"score": self.knowledge, "evidence": self.evidence.get("knowledge", "")},
            "discrimination": {"score": self.discrimination, "evidence": self.evidence.get("discrimination", "")},
        }


@dataclass
class QuestionReviewResult:
    """题目级四维评审结果：四维评分 + 综合分 + 判定状态。"""

    scores: ReviewScores
    composite: float
    status: str  # passed / warning / blocked
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "scores": self.scores.as_dict(),
            "composite": round(self.composite, 2),
            "status": self.status,
            "reason": self.reason,
        }


def _clamp(score: float) -> float:
    return max(0.0, min(100.0, float(score)))


def composite_score(
    scientificity: float, difficulty: float, knowledge: float, discrimination: float
) -> float:
    """固定权重综合分：0.4×科学性 + 0.25×难度 + 0.2×知识点 + 0.15×区分度。"""
    return (
        WEIGHTS["scientificity"] * scientificity
        + WEIGHTS["difficulty"] * difficulty
        + WEIGHTS["knowledge"] * knowledge
        + WEIGHTS["discrimination"] * discrimination
    )


def classify_review(composite: float, scientificity: float) -> tuple[str, str]:
    """综合分映射 + 科学性单维红线（doc 26 / 设计 D2）。

    科学性 <70 → blocked（硬阻断，无论综合分）；composite ≥80 → passed；
    70-79 → warning；<70 → blocked。
    返回 (状态, 判定理由)。
    """
    if scientificity < SCIENTIFICITY_RED_LINE:
        return "blocked", "科学性单维低于 70，硬阻断"
    if composite >= PASS_THRESHOLD:
        return "passed", "综合分 ≥80，题目级通过"
    if composite >= WARNING_THRESHOLD:
        return "warning", "综合分 70-79，题目级需复核"
    return "blocked", "综合分 <70，题目级阻断"


def parse_review_response(text: str) -> ReviewScores:
    """解析 LLM 返回的四维评分 JSON，容忍 markdown 围栏与前后杂文。"""
    if not text or not text.strip():
        raise ReviewLLMError("LLM 评审响应为空")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ReviewLLMError("LLM 评审响应中未找到 JSON 对象")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ReviewLLMError(f"LLM 评审响应 JSON 解析失败: {e}") from e

    scores: dict[str, float] = {}
    for dim in REVIEW_DIMENSIONS:
        raw = data.get(dim, data.get(f"{dim}_score", 0))
        scores[dim] = _clamp(raw)
    evidence = {
        dim: str(data.get("evidence", {}).get(dim, data.get(f"{dim}_evidence", "")) or "")
        for dim in REVIEW_DIMENSIONS
    }
    return ReviewScores(**scores, evidence=evidence)


def _build_review_prompt(question: dict) -> list[dict]:
    """组装四维评审 prompt（系统 + 用户）。"""
    system = (
        "你是中学化学试题质量评审专家。对给定题目按四个维度各打 0-100 整数分，"
        "并给出一句简短理由（evidence）。四维：科学性（化学知识与表达是否准确无误）、"
        "难度匹配（与标注难度标签是否一致）、知识点覆盖（考查点是否明确且贴合目标知识点）、"
        "区分度（能否区分不同水平学生）。"
        "仅输出 JSON："
        '{"scientificity": 90, "difficulty": 80, "knowledge": 85, "discrimination": 75, '
        '"evidence": {"scientificity": "...", "difficulty": "...", "knowledge": "...", "discrimination": "..."}}'
    )
    user = (
        "题目正文：{content}\n"
        "选项：{options}\n"
        "标准答案：{answer}\n"
        "答案解析：{analysis}\n"
        "知识点：{knowledge_points}\n"
        "标注难度：{difficulty}\n"
    ).format(
        content=question.get("content", ""),
        options=question.get("options") or [],
        answer=question.get("answer", ""),
        analysis=question.get("analysis", ""),
        knowledge_points=question.get("knowledge_points", ""),
        difficulty=question.get("difficulty", ""),
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _chat(provider: str, messages: list[dict]) -> str:
    """单 Provider 调用。真实 HTTP 传输在 LLM 基础设施落地时接入；当前无密钥即报错。"""
    if not settings.llm_api_key:
        raise ReviewLLMError(f"LLM Provider '{provider}' 未配置 llm_api_key")
    raise ReviewLLMError(f"Provider '{provider}' 的 HTTP 传输尚未接入（评审服务待 LLM 基建）")


class FallbackReviewLLMClient:
    """默认三级 Fallback 客户端：MiMo-V2.5 → qwen-turbo → DeepSeek，每级 3 次重试 + 指数退避。"""

    def __init__(self, provider_chain: tuple[str, ...] = PROVIDER_CHAIN,
                 retries: int = RETRY_PER_PROVIDER) -> None:
        self.provider_chain = provider_chain
        self.retries = retries

    @property
    def available(self) -> bool:
        """LLM 评审在当前环境是否可用（未配置 llm_api_key → 降级方程式级，不 500）。"""
        return bool(settings.llm_api_key)

    def complete(self, messages: list[dict]) -> str:
        last_error: Exception | None = None
        for provider in self.provider_chain:
            for attempt in range(self.retries):
                try:
                    return _chat(provider, messages)
                except ReviewLLMError as e:
                    last_error = e
                    if attempt < self.retries - 1:
                        time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
        raise ReviewLLMError(f"三级 Fallback 全部失败: {last_error}")


def review_question(question: dict, client: ReviewLLMClient | None = None) -> QuestionReviewResult:
    """执行题目级四维评审：LLM 打分 → 权重合成 → 阈值判定。"""
    client = client or FallbackReviewLLMClient()
    messages = _build_review_prompt(question)
    raw = client.complete(messages)
    scores = parse_review_response(raw)
    composite = composite_score(
        scores.scientificity, scores.difficulty, scores.knowledge, scores.discrimination
    )
    status, reason = classify_review(composite, scores.scientificity)
    return QuestionReviewResult(scores=scores, composite=composite, status=status, reason=reason)
