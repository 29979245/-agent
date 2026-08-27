"""LLM 批改引擎（设计 D5）。

答案来源选择（题库匹配 > 教师录入 > LLM 自判）→ 答案标准化比较（去空白+转大写+相等，
确定性）→ 主观题可选走 LLM 语义判定。自判安全模式：无法确定答案标记需人工复核，
不自动判对错（对接 ADR 0003 模式3 落库）。
"""
from __future__ import annotations

import asyncio
import json

from app.services.diagnosis.llm_diagnosis import (
    FallbackDiagnosisLLMClient,
    extract_json,
)

# 标准答案标记为自判模式：不自动判对错，需人工复核
AUTO_MARKER = "AUTO"

SUBJECTIVE_SYSTEM_PROMPT = (
    "你是中学化学批改助手。根据题目、标准答案与学生答案判定正确与否。"
    "仅输出一个 JSON 对象："
    '{"is_correct": true, "review_needed": false, "reason": "判定理由（1 句）"}。'
    "无法确定时 review_needed 置 true、is_correct 置 false。"
)


def normalize_answer(text) -> str:
    """答案标准化：去首尾空白 + 转大写。"""
    return (text or "").strip().upper()


def compare_answer(student_answer, standard_answer) -> bool:
    """标准化比较：去空白+转大写后完全相等才正确。

    学生/标准答案为空白、或标准答案为自判模式（AUTO）时判错。
    """
    if not student_answer or not standard_answer:
        return False
    if standard_answer.strip().upper() == AUTO_MARKER:
        return False
    return normalize_answer(student_answer) == normalize_answer(standard_answer)


def select_answer_source(has_exam: bool, bank_answers: dict | None, teacher_answers: dict | None) -> tuple[str, dict]:
    """答案来源优先级：题库匹配（有考试）→ 教师录入 → LLM 自判。"""
    if has_exam and bank_answers:
        return "bank", bank_answers
    if teacher_answers:
        return "teacher", teacher_answers
    return "self", {}


def grade_submission(
    answers: list[dict],
    *,
    has_exam: bool = False,
    bank_answers: dict | None = None,
    teacher_answers: dict | None = None,
) -> dict:
    """逐题批改：答案来源选择 + 标准化比较 + 自判安全模式。

    items 形如 [{"question_no", "student_answer", "standard_answer",
                 "is_correct": bool|None, "review_needed": bool}]；
    source=self 时全部题目 is_correct=None（不判对错）且 review_needed=True。
    """
    source, standards = select_answer_source(has_exam, bank_answers, teacher_answers)
    items: list[dict] = []
    manual_review = False
    for item in answers:
        qno = item.get("question_no")
        student = (item.get("answer") or "").strip()
        if source == "self":
            items.append(
                {
                    "question_no": qno,
                    "student_answer": student,
                    "standard_answer": "",
                    "is_correct": None,
                    "review_needed": True,
                }
            )
            manual_review = True
            continue
        standard = standards.get(qno, "")
        review_needed = False
        if not standard or standard.strip().upper() == AUTO_MARKER:
            review_needed = True
            manual_review = True
        items.append(
            {
                "question_no": qno,
                "student_answer": student,
                "standard_answer": standard,
                "is_correct": compare_answer(student, standard),
                "review_needed": review_needed,
            }
        )
    return {"source": source, "items": items, "manual_review": manual_review}


# ---------------- 主观题 LLM 语义批改（5.4） ----------------


def build_subjective_prompt(question: str, student_answer: str, standard_answer: str) -> list[dict]:
    return [
        {"role": "system", "content": SUBJECTIVE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"题目：{question}\n标准答案：{standard_answer}\n学生答案：{student_answer}",
        },
    ]


class SubjectiveGradingError(ValueError):
    """主观题判定响应无效（无 JSON / 解析失败）。"""


def parse_subjective_result(text: str) -> dict:
    """结构化解析主观题 LLM 判定；无效响应抛 SubjectiveGradingError。"""
    raw = extract_json(text or "")
    if raw is None:
        raise SubjectiveGradingError("主观题判定响应中未找到 JSON")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SubjectiveGradingError(f"主观题判定 JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise SubjectiveGradingError("主观题判定 JSON 顶层非对象")
    return {
        "is_correct": bool(data.get("is_correct", False)),
        "review_needed": bool(data.get("review_needed", False)),
        "reason": str(data.get("reason", "")),
    }


async def grade_subjective(
    question: str,
    student_answer: str,
    standard_answer: str,
    client=None,
) -> dict:
    """主观题 LLM 语义批改：同步调用 LLM + 结构化解包；解析失败容错标记人工复核。"""
    llm = client or FallbackDiagnosisLLMClient()
    try:
        messages = build_subjective_prompt(question, student_answer, standard_answer)
        raw = await asyncio.to_thread(llm.complete, messages)
        return parse_subjective_result(raw)
    except Exception as e:  # 容错：判定失败不抛异常，标记人工复核
        return {
            "is_correct": False,
            "review_needed": True,
            "reason": f"主观题判定失败，需人工复核: {e}",
        }
