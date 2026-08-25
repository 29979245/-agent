"""双层审核合成与 audit_report 结构（设计 D3/D4）。

- compose_overall_status：方程式级 + 题目级 合成整体状态。任一层 blocked → blocked；
  否则任一层 warning → warning；否则 passed。纯函数，作为双层合成评测的单一真值来源。
- build_audit_report：序列化双层审核报告 JSON（equation_level + question_level +
  composite + overall_status + meta），可直接写入 Question.audit_report 字段。
  题目级缺失（手动录入 / OCR 导入只过方程式级）时 question_level 为 None。
"""
from __future__ import annotations

from app.services.audit.equation import EquationAuditReport, audit_equation, extract_equations
from app.services.audit.review import (
    QuestionReviewResult,
    ReviewLLMClient,
    review_question,
)

# 双层报告字段标签（与设计 D4 结构一致）
EQUATION_DIM_LABELS = ("系数配平", "反应条件", "产物正确性", "分子结构")
EQUATION_DIM_FIELDS = ("balance", "condition", "product", "structure")
QUESTION_DIM_LABELS = ("科学性", "难度匹配", "知识点覆盖", "区分度")
QUESTION_DIM_FIELDS = ("scientificity", "difficulty", "knowledge", "discrimination")


def compose_overall_status(
    equation_status: str, question_status: str | None = None
) -> str:
    """双层合成：任一层 blocked → blocked；否则任一层 warning → warning；否则 passed。

    题目级缺失（None）时整体状态即方程式级状态。
    """
    statuses = [equation_status]
    if question_status is not None:
        statuses.append(question_status)
    if any(s == "blocked" for s in statuses):
        return "blocked"
    if any(s == "warning" for s in statuses):
        return "warning"
    return "passed"


def _equation_level(equation: EquationAuditReport) -> dict:
    dims: dict[str, dict] = {}
    for label, field in zip(EQUATION_DIM_LABELS, EQUATION_DIM_FIELDS):
        res = getattr(equation, field)
        dims[label] = {"status": res.status, "evidence": res.message}
    return {**dims, "overall_status": equation.overall_status}


def _question_level(review: QuestionReviewResult) -> dict:
    dims: dict[str, dict] = {}
    for label, field in zip(QUESTION_DIM_LABELS, QUESTION_DIM_FIELDS):
        dims[label] = {
            "score": getattr(review.scores, field),
            "evidence": review.scores.evidence.get(field, ""),
        }
    return {**dims, "composite": round(review.composite, 2), "status": review.status, "reason": review.reason}


def build_audit_report(
    equation_report: EquationAuditReport | None = None,
    question_review: QuestionReviewResult | None = None,
    meta: dict | None = None,
) -> dict:
    """构建双层审核报告 JSON。

    方程式报告缺失（题目无方程式可校验）时 equation_level 记 passed 并附说明；
    题目级评审缺失（手动/OCR 只过方程式级）时 question_level 为 None，
    整体状态由方程式级状态直接决定。
    """
    meta = {**{"regeneration_attempts": 0, "generation_failed": False}, **(meta or {})}
    if equation_report is None:
        equation_status = "passed"
        equation_level = {"overall_status": "passed", "note": "无方程式可校验"}
    else:
        equation_status = equation_report.overall_status
        equation_level = _equation_level(equation_report)
    if question_review is None:
        overall_status = compose_overall_status(equation_status, None)
    else:
        overall_status = compose_overall_status(equation_status, question_review.status)
    return {
        "equation_level": equation_level,
        "question_level": _question_level(question_review) if question_review else None,
        "overall_status": overall_status,
        "meta": meta,
    }


def run_content_audit(
    content: str,
    source: str,
    review_client: ReviewLLMClient | None = None,
    question_data: dict | None = None,
) -> tuple[EquationAuditReport | None, QuestionReviewResult | None, dict]:
    """按题目来源执行对应触发范围的审核（设计 D7 / task 6.1）。

    source == "ai" → 两层审核（方程式级 + 题目级四维评审）；
    source == "manual"/"ocr" → 只过方程式级硬闸，跳过题目级。
    AI 来源但评审客户端不可用（未配置 llm_api_key）→ 降级方程式级硬闸，
    meta 记 review_note=llm_unconfigured，不 500。
    返回 (方程式报告, 题目级评审, 双层报告)。
    """
    equations = extract_equations(content)
    equation_report = audit_equation(equations[0]) if equations else None

    review: QuestionReviewResult | None = None
    meta: dict = {}
    if source == "ai":
        if review_client is None or getattr(review_client, "available", True):
            review = review_question(question_data or {"content": content}, review_client)
        else:
            meta["review_note"] = "llm_unconfigured"

    report = build_audit_report(equation_report, review, meta=meta)
    return equation_report, review, report
