"""提交批改后异步副作用（design.md D3）：复习同步 + 规则融合诊断降级。

BackgroundTasks 在响应返回后以独立会话执行，不阻塞提交接口。诊断 LLM 不可用
（_chat 桩）时降级走规则引擎 + 融合单侧（source=rule），失败落 error 标志不阻断。
"""
from __future__ import annotations

import logging

from app.services.diagnosis.fusion import FLAG_ERROR, fuse
from app.services.diagnosis.llm_diagnosis import (
    DiagnosisLLMClient,
    FallbackDiagnosisLLMClient,
    diagnose_llm,
)
from app.services.diagnosis.rule_engine import ChemistryRuleEngine
from app.services.exercise.spaced_repetition import sync_review_tasks
from app.db.models import Question, StudentAnswer

logger = logging.getLogger(__name__)

DIAGNOSIS_VERSION = "1.0"

_engine = ChemistryRuleEngine()


def get_diagnosis_client() -> DiagnosisLLMClient:
    """诊断 LLM 客户端（默认三级 Fallback；LLM 未接入时 _chat 立即报错 → 走规则单侧）。"""
    return FallbackDiagnosisLLMClient()


def run_submit_side_effects(
    session_factory,
    student_id: int,
    wrong_question_ids: list[int],
    client: DiagnosisLLMClient | None = None,
) -> None:
    """独立会话执行：复习同步去重 + 错题规则融合诊断。异常不向响应传播。"""
    db = session_factory()
    try:
        if wrong_question_ids:
            sync_review_tasks(db, student_id, wrong_question_ids)
            _rule_fallback_diagnosis(db, student_id, wrong_question_ids, client)
        db.commit()
    except Exception:  # noqa: BLE001 —— 后台副作用失败仅记日志，不阻塞主流程
        logger.exception("提交后副作用执行失败：student_id=%s", student_id)
        db.rollback()
    finally:
        db.close()


def _rule_fallback_diagnosis(
    db, student_id: int, question_ids: list[int], client: DiagnosisLLMClient | None = None
) -> None:
    """对本次错题执行诊断：LLM 不可用即降级规则引擎单路（source=rule），不阻塞。"""
    client = client or get_diagnosis_client()
    llm_available = bool(getattr(client, "available", False))
    answers = (
        db.query(StudentAnswer)
        .filter(
            StudentAnswer.student_id == student_id,
            StudentAnswer.question_id.in_(question_ids),
            StudentAnswer.is_correct.is_(False),
        )
        .all()
    )
    for ans in answers:
        question = db.get(Question, ans.question_id)
        content = question.content if question else ""
        rule = _engine.top_diagnosis(ans.answer_text, content)
        llm = (
            diagnose_llm(
                {
                    "question": content,
                    "answer": ans.answer_text,
                    "correct_answer": question.answer if question else "",
                    "history": [],
                },
                client,
            )
            if llm_available
            else None
        )
        fused = fuse(rule, llm, question_id=ans.question_id)
        ans.fused_conf = fused.fused_conf
        ans.rule_conf = fused.rule_conf
        ans.llm_conf = fused.llm_conf
        ans.diagnosis_version = DIAGNOSIS_VERSION
        ans.diagnosis_source = fused.source
        detail = {
            "reasoning": fused.reasoning,
            "suggestion": fused.suggestion,
            "remediation": fused.remediation,
        }
        if fused.diagnosis_flag == FLAG_ERROR or (llm is not None and llm.is_error):
            ans.barrier_type = None
            ans.diagnosis_flag = FLAG_ERROR
            detail["error"] = (llm.error if llm and llm.is_error else "") or "诊断失败"
        else:
            ans.barrier_type = fused.barrier_type
            ans.diagnosis_flag = fused.diagnosis_flag
        ans.diagnosis_detail = detail
