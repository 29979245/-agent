"""OCR 批改组工具（doc 30 §3.5，3 工具）——进度查询 / 批量批改 / 保存落库+诊断。

契约要点：
- 三工具均仅 teacher 角色可调用（doc 30 §3.5），非教师返回 ForbiddenError。
- query_ocr_progress 只读：aggregate_tasks 聚合批次进度。
- grade_answer_sheets 不写任何作答记录（StudentAnswer）：批改只计算，中间结果挂 task.result['grading']
  并推进会话到 grading，供 save_grading_results 读取后落 StudentAnswer（save 是唯一落库点）。
- save_grading_results 写 StudentAnswer + 触发障碍诊断（复用 API 层 run_llm_batch），返回保存数量 + 诊断确认。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.api.v1.diagnosis import get_diagnosis_client, run_llm_batch
from app.core.exceptions import APIException, ForbiddenError, NotFoundError
from app.db.models import Class, ExamRecord, Grade, StudentAnswer
from app.services.ocr import batch
from app.agents.tools.context import ToolContext, user_role, user_school_id
from app.agents.tools import tools_memory

# ---------------------------------------------------------------- 参数 Schema

class QueryOcrProgressArgs(BaseModel):
    batch_id: int = Field(..., description="批次 ID")
    teacher_id: Optional[int] = Field(default=None, description="教师 ID（可选，缺省按登录角色）")


class GradeAnswerSheetsArgs(BaseModel):
    batch_id: int = Field(..., description="批次 ID")
    exam_id: Optional[int] = Field(default=None, description="考试 ID")
    teacher_id: Optional[int] = Field(default=None, description="教师 ID（可选，缺省按登录角色）")


class SaveGradingResultsArgs(BaseModel):
    batch_id: int = Field(..., description="批次 ID")
    exam_id: Optional[int] = Field(default=None, description="考试 ID")
    teacher_id: Optional[int] = Field(default=None, description="教师 ID（可选，缺省按登录角色）")


# ---------------------------------------------------------------- 辅助

def _teacher_check(ctx: ToolContext) -> None:
    """OCR 批改仅教师可操作（doc 30 §3.5）；非教师 → ForbiddenError。"""
    if user_role(ctx) != "teacher":
        raise ForbiddenError(detail="仅教师可执行 OCR 批改操作", error_code="OCR_TEACHER_ONLY")


def _require_exam_in_teacher_school(ctx: ToolContext, exam_id: Optional[int]) -> None:
    """教师仅可对本校考试批改/落库：考试不存在 404、跨校考试 403（防跨校写 StudentAnswer）。"""
    if user_role(ctx) != "teacher" or exam_id is None:
        return
    school_id = (
        ctx.db.query(Grade.school_id)
        .select_from(ExamRecord)
        .join(Class, Class.id == ExamRecord.class_id)
        .join(Grade, Grade.id == Class.grade_id)
        .filter(ExamRecord.id == exam_id)
        .scalar()
    )
    if school_id is None:
        raise NotFoundError(detail="考试不存在", error_code="EXAM_NOT_FOUND", suggestion="请检查考试 id")
    if school_id != user_school_id(ctx):
        raise ForbiddenError(
            detail="无权操作其他学校的考试",
            error_code="EXAM_SCOPE_MISMATCH",
            suggestion="仅可对本校考试触发批改/保存",
        )


def _api_error(exc: APIException) -> dict:
    """服务层 APIException → 可读错误 dict（保留 error_code，供 Agent 透出）。"""
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return {
        "error": detail.get("error_code", "tool_failed"),
        "message": detail.get("detail", str(exc)),
        "_guard_error": True,
    }


def _push_exam_diagnosis_memory(ctx: ToolContext, exam_id: int) -> int:
    """D3 写接线②：OCR 保存触发诊断后，把本场考试已诊断（barrier_type 非空）学生画像写长期记忆。"""
    if ctx.db is None or getattr(ctx.memory, "long_term", None) is None:
        return 0
    ctx.db.flush()  # 诊断把 barrier_type 落在未提交会话：flush 后查询可见（生产 run_llm_batch 内部已 commit）
    student_ids = (
        ctx.db.query(StudentAnswer.student_id)
        .filter(StudentAnswer.exam_id == exam_id, StudentAnswer.barrier_type.is_not(None))
        .distinct()
        .all()
    )
    count = 0
    for (sid,) in student_ids:
        if tools_memory.push_student_diagnosis_memory(ctx, sid, source="ocr_save_diagnosis"):
            count += 1
    return count


def _get_session(ctx: ToolContext, batch_id: int):
    """取批次并按校隔离。跨校访问是越权 → 抛 ForbiddenError；批次不存在 → 可读 dict 供 Agent 恢复。"""
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    try:
        return batch.get_session_or_404(ctx.db, batch_id, school_id=user_school_id(ctx))
    except ForbiddenError:
        raise
    except NotFoundError as exc:
        return _api_error(exc)


# ---------------------------------------------------------------- 工具实现

def query_ocr_progress(ctx: ToolContext, batch_id: int, teacher_id: Optional[int] = None) -> dict:
    """批次 OCR 进度聚合（只读，doc 30 §3.5 工具 10）。"""
    _teacher_check(ctx)
    session = _get_session(ctx, batch_id)
    if isinstance(session, dict):
        return session
    return batch.aggregate_tasks(ctx.db, session)


def grade_answer_sheets(
    ctx: ToolContext,
    batch_id: int,
    exam_id: Optional[int] = None,
    teacher_id: Optional[int] = None,
) -> dict:
    """批量批改：对 done 任务逐题 LLM 批改，只算不落库（doc 30 §3.5 工具 11）。"""
    _teacher_check(ctx)
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    try:
        _require_exam_in_teacher_school(ctx, exam_id)
        session = batch.get_session_or_404(ctx.db, batch_id, school_id=user_school_id(ctx))
        return batch.run_grading(ctx.db, session, exam_id=exam_id)
    except ForbiddenError:
        raise
    except NotFoundError:
        raise
    except APIException as exc:
        return _api_error(exc)


def save_grading_results(
    ctx: ToolContext,
    batch_id: int,
    exam_id: Optional[int] = None,
    teacher_id: Optional[int] = None,
) -> dict:
    """保存批改结果：写 StudentAnswer + 触发障碍诊断（doc 30 §3.5 工具 12）。"""
    _teacher_check(ctx)
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    try:
        _require_exam_in_teacher_school(ctx, exam_id)
        session = batch.get_session_or_404(ctx.db, batch_id, school_id=user_school_id(ctx))
        result = batch.save_grading_results(
            ctx.db, session, exam_id=exam_id, school_id=user_school_id(ctx)
        )
    except ForbiddenError:
        raise
    except NotFoundError:
        raise
    except APIException as exc:
        return _api_error(exc)

    # 模式1 落库成功（saved>0）且有考试 → 触发障碍诊断（镜像 ocr.py grading_save_endpoint）
    run_exam_id = exam_id if exam_id is not None else session.exam_id
    if run_exam_id is not None and result["saved"] > 0:
        try:
            run_llm_batch(ctx.db, run_exam_id, get_diagnosis_client(), ctx.user)
            result["diagnosis"] = {"status": "triggered"}
            # D3 写接线②：诊断完成 → 已诊断学生画像写长期记忆（best-effort，不阻塞）
            result["diagnosis"]["memory_written"] = _push_exam_diagnosis_memory(ctx, run_exam_id)
        except Exception:  # noqa: BLE001 —— 落库已提交，诊断失败不回滚保存结果
            ctx.db.rollback()
            result["diagnosis"] = {"status": "failed"}
    return result
