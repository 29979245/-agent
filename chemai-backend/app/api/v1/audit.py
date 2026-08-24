"""审核工作流 API（audit-engine-api）。

挂载于 /api/question，四端点全部 teacher+ 权限（require_permission("question", "create")）。
- POST /generate       生成并内嵌双层审核报告（AI 走两层 / manual、ocr 只方程式级）
- POST /audit          对已存储题目重新审核，返回双层报告
- POST /{id}/approve   教师批准：passed 入库 / warning 带复核标记放行
- POST /{id}/regenerate 重生成 + 两层重审

审核触发范围按题目来源区分（设计 D7）：question.source == "ai" 走两层审核；
manual / ocr 只过方程式级硬闸（题目级跳过）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.permissions import require_permission
from app.db.models import Question
from app.db.models.enums import AuditStatus, Difficulty, QuestionSource
from app.db.session import get_db
from app.services.audit.review import FallbackReviewLLMClient, ReviewLLMClient
from app.services.audit.state_machine import (
    AuditState,
    apply_action,
    run_audit_cycle,
)
from app.services.audit.workflow import run_content_audit

audit_logger = logging.getLogger("chemai.audit")
audit_router = APIRouter()


def get_review_client() -> ReviewLLMClient:
    """题目级评审客户端（FastAPI 依赖，测试可覆盖注入 Mock）。"""
    return FallbackReviewLLMClient()


class GenerateRequest(BaseModel):
    content: str = Field(..., min_length=1)
    options: list[str] = []
    answer: str = ""
    analysis: str = ""
    knowledge_points: str = ""
    difficulty: str = "medium"
    source: str = "ai"  # ai / manual / ocr


class AuditRequest(BaseModel):
    question_id: int


class RegenerateRequest(BaseModel):
    content: str | None = None
    answer: str | None = None
    analysis: str | None = None
    knowledge_points: str | None = None


def _question_data(question: Question) -> dict:
    return {
        "content": question.content,
        "options": question.options or [],
        "answer": question.answer,
        "analysis": question.analysis or "",
        "knowledge_points": question.knowledge_points or "",
        "difficulty": question.difficulty.value if question.difficulty else "medium",
    }


def _audit_and_store(
    db: Session,
    question: Question,
    client: ReviewLLMClient,
    meta: dict | None = None,
) -> dict:
    """执行按来源触发的审核，写回 audit_report + audit_status，返回双层报告。

    meta 为 None（重审）时保留题目既有 meta（regeneration_attempts / review_flag 等不丢）；
    显式传入 meta（生成/重生成路径由状态机产出最终 meta）时以其覆盖。
    """
    _, _, report = run_content_audit(
        question.content, question.source.value, client, _question_data(question)
    )
    if meta is None:
        existing = (question.audit_report or {}).get("meta") or {}
        report["meta"] = {**report.get("meta", {}), **existing}
    else:
        report["meta"] = dict(meta)
    question.audit_report = report
    question.audit_status = report["overall_status"]
    db.commit()
    return report


def _run_generation_cycle(
    db: Session, question: Question, client: ReviewLLMClient
) -> tuple[AuditState, dict]:
    """AI 生成自动重生成循环（spec 审核状态机 4.2）：blocked 自动重生成 ≤3 次，
    每次重新两层审核；3 次仍 blocked 置 meta.generation_failed=true（不自动替换）。

    生成管线（services/question）落地前，循环内重审当前内容——blocked 内容因此
    在 API 层可触达"重生成耗尽 → 出题失败"场景。返回 (最终状态, 最终双层报告)。
    """
    last_report: dict = {}

    def _generate() -> tuple:
        eq, review, report = run_content_audit(
            question.content, question.source.value, client, _question_data(question)
        )
        last_report.clear()
        last_report.update(report)
        return eq, review

    state, _ = run_audit_cycle(_generate, AuditState(overall_status="passed"))
    last_report["meta"] = {**last_report.get("meta", {}), **state.to_meta()}
    question.audit_report = last_report
    question.audit_status = last_report["overall_status"]
    db.commit()
    return state, last_report


@audit_router.post("/generate")
@require_permission("question", "create")
def generate(
    request: Request,
    payload: GenerateRequest,
    db: Session = Depends(get_db),
    client: ReviewLLMClient = Depends(get_review_client),
) -> dict:
    """生成题目并内嵌双层审核报告。AI 走两层；manual / ocr 只过方程式级。"""
    source = QuestionSource(payload.source)
    question = Question(
        content=payload.content,
        options=payload.options,
        answer=payload.answer,
        analysis=payload.analysis,
        knowledge_points=payload.knowledge_points,
        difficulty=Difficulty(payload.difficulty),
        source=source,
        audit_status=AuditStatus.passed,
        audit_report={},
    )
    db.add(question)
    db.flush()
    if source == QuestionSource.ai:
        state, report = _run_generation_cycle(db, question, client)
        generation_failed = state.generation_failed
    else:
        report = _audit_and_store(db, question, client)
        generation_failed = False
    audit_logger.info(
        "question_generated",
        extra={"event": "question_generated", "question_id": question.id,
               "source": source.value, "overall": report["overall_status"]},
    )
    return {
        "question_id": question.id,
        "audit_report": report,
        "overall_status": report["overall_status"],
        "generation_failed": generation_failed,
    }


@audit_router.post("/audit")
@require_permission("question", "create")
def reaudit(
    request: Request,
    payload: AuditRequest,
    db: Session = Depends(get_db),
    client: ReviewLLMClient = Depends(get_review_client),
) -> dict:
    """对已存储题目重新审核，返回双层报告。"""
    question = db.get(Question, payload.question_id)
    if question is None:
        raise NotFoundError()
    report = _audit_and_store(db, question, client)
    return {
        "question_id": question.id,
        "audit_report": report,
        "overall_status": report["overall_status"],
    }


@audit_router.post("/{question_id}/approve")
@require_permission("question", "create")
def approve(
    request: Request,
    question_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """教师批准：passed 入库 / warning 放行带复核标记；blocked 不可批准。"""
    question = db.get(Question, question_id)
    if question is None:
        raise NotFoundError()
    current_status = question.audit_status.value if question.audit_status else "blocked"
    state = AuditState(
        overall_status=current_status,
        regeneration_attempts=(question.audit_report or {}).get("meta", {}).get(
            "regeneration_attempts", 0
        ),
    )
    try:
        next_state = apply_action(state, "approve")
    except ValueError:
        raise HTTPException(status_code=400, detail="blocked 题目不可批准，应自动重生成")

    report = dict(question.audit_report or {})
    meta = dict(report.get("meta", {}))
    meta["status"] = next_state.status  # "approved" = 已入库
    meta["review_flag"] = next_state.review_flag
    report["meta"] = meta
    question.audit_report = report
    db.commit()
    audit_logger.info(
        "question_approved",
        extra={"event": "question_approved", "question_id": question.id,
               "overall": current_status, "review_flag": next_state.review_flag},
    )
    return {
        "question_id": question.id,
        "status": "approved",
        "review_flag": next_state.review_flag,
    }


@audit_router.post("/{question_id}/regenerate")
@require_permission("question", "create")
def regenerate(
    request: Request,
    question_id: int,
    payload: RegenerateRequest,
    db: Session = Depends(get_db),
    client: ReviewLLMClient = Depends(get_review_client),
) -> dict:
    """教师触发重生成：更新题目内容并重新执行两层审核（仅 warning / blocked 可重生成）。"""
    question = db.get(Question, question_id)
    if question is None:
        raise NotFoundError()
    if question.audit_status == AuditStatus.passed:
        raise HTTPException(status_code=400, detail="passed 题目无需重生成")
    if payload.content is not None:
        question.content = payload.content
    if payload.answer is not None:
        question.answer = payload.answer
    if payload.analysis is not None:
        question.analysis = payload.analysis
    if payload.knowledge_points is not None:
        question.knowledge_points = payload.knowledge_points

    report = _audit_and_store(db, question, client)
    meta = dict(report.get("meta", {}))
    meta["regeneration_attempts"] = meta.get("regeneration_attempts", 0) + 1
    # 手动重生成通过后清除自动生成失败的标记：已修复题目不应继续显示"出题失败"
    if report["overall_status"] in ("passed", "warning"):
        meta["generation_failed"] = False
    report["meta"] = meta
    question.audit_report = report
    db.commit()
    return {
        "question_id": question.id,
        "audit_report": report,
        "overall_status": report["overall_status"],
    }
