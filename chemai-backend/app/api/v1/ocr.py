"""OCR 批改判卷 API（设计 D8，仅批改判卷线；题库导入/预览线不在本 change）。

- /api/ocr/tasks/batch           批量上传建批次（空文件 400 / 格式 415 / 超限 413）
- /api/ocr/tasks/batch/{id}      批次状态聚合（total/done/failed/pending）
- /api/ocr/tasks/{id}/retry      失败任务重试（failed→pending）
- /api/ocr/tasks                 教师任务列表
- /api/ocr/services/status       三引擎服务可用性检查
- /api/grading/run               触发批改（无 done 任务 404）
- /api/grading/save              保存结果（分档落库 + 触发诊断）
- /api/grading/results/{id}      查询批改结果
"""
from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.diagnosis import get_diagnosis_client, run_llm_batch
from app.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import require_permission
from app.db.models import Class, ExamRecord, Grade, OCRTask, UploadSession
from app.db.session import get_db
from app.services.diagnosis.llm_diagnosis import DiagnosisLLMClient
from app.services.ocr import batch
from app.services.ocr.engines.mineru_engine import check_available
from app.services.ocr.scheduler import retry_task

ocr_router = APIRouter()
grading_router = APIRouter()


class GradingRunRequest(BaseModel):
    batch_id: int
    exam_id: int | None = None
    teacher_answers: dict[int, str] | None = None  # 教师录入答案：{题号: 标准答案}


class GradingSaveRequest(BaseModel):
    batch_id: int
    exam_id: int | None = None


def _tenant_school_id(user) -> int | None:
    """教师按校隔离，其余角色不限制；约定同 exam.list_classes（仅 role=='teacher' 收紧）。"""
    return user.school_id if user.role == "teacher" else None


def _require_exam_in_teacher_school(db: Session, user, exam_id: int | None) -> None:
    """教师仅可对本校考试批改/落库：考试不存在 404、跨校考试 403（防跨校写 StudentAnswer）。"""
    if user.role != "teacher" or exam_id is None:
        return
    school_id = (
        db.query(Grade.school_id)
        .select_from(ExamRecord)
        .join(Class, Class.id == ExamRecord.class_id)
        .join(Grade, Grade.id == Class.grade_id)
        .filter(ExamRecord.id == exam_id)
        .scalar()
    )
    if school_id is None:
        raise NotFoundError(detail="考试不存在", error_code="EXAM_NOT_FOUND", suggestion="请检查考试 id")
    if school_id != user.school_id:
        raise ForbiddenError(
            detail="无权操作其他学校的考试",
            error_code="EXAM_SCOPE_MISMATCH",
            suggestion="仅可对本校考试触发批改/保存",
        )


# ---------------- /api/ocr ----------------


@ocr_router.post("/tasks/batch")
@require_permission("ocr", "create")
def create_batch_endpoint(
    request: Request,
    files: list[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """批量上传建批次：保存文件 + 创建 pending OCRTask（空文件列表 400）。"""
    session, task_ids = batch.create_batch(
        db, files, settings.ocr_upload_dir, school_id=_tenant_school_id(request.state.user)
    )
    return {
        "batch_id": session.id,
        "count": len(task_ids),
        "task_ids": task_ids,
        "status": session.status.value,
    }


@ocr_router.get("/tasks/batch/{batch_id}")
@require_permission("ocr", "read")
def batch_status_endpoint(
    request: Request, batch_id: int, db: Session = Depends(get_db)
) -> dict:
    """批次状态聚合：total/done/failed/pending/processing + 任务明细。"""
    return batch.aggregate_tasks(
        db, batch.get_session_or_404(db, batch_id, school_id=_tenant_school_id(request.state.user))
    )


@ocr_router.post("/tasks/{task_id}/retry")
@require_permission("ocr", "update")
def retry_task_endpoint(
    request: Request, task_id: int, db: Session = Depends(get_db)
) -> dict:
    """失败任务重试：failed → pending，清空错误与识别结果（幂等）。"""
    task = db.get(OCRTask, task_id)
    if task is None:
        raise NotFoundError()
    school_id = _tenant_school_id(request.state.user)
    if school_id is not None:
        session = db.get(UploadSession, task.session_id)
        if session is None or session.school_id != school_id:
            raise ForbiddenError(
                detail="无权访问该任务",
                error_code="TASK_SCOPE_MISMATCH",
                suggestion="仅可操作本校答题卡任务",
            )
    before = task.status
    retry_task(db, task)
    db.commit()
    if task.status == before:  # 非 failed 任务为 no-op，如实告知而非谎报已重置
        return {"task_id": task.id, "status": task.status.value, "message": f"任务已是 {before.value}，无需重试"}
    return {"task_id": task.id, "status": task.status.value, "message": "已重置为待处理"}


@ocr_router.get("/tasks")
@require_permission("ocr", "read")
def task_list_endpoint(request: Request, db: Session = Depends(get_db)) -> dict:
    """教师任务列表：按校隔离、全批次倒序，含各批次任务明细。"""
    return {"batches": batch.teacher_batch_list(db, school_id=_tenant_school_id(request.state.user))}


@ocr_router.get("/services/status")
@require_permission("ocr", "read")
def services_status_endpoint(request: Request) -> dict:
    """三引擎服务可用性检查（设计 11.6）：ocr/mineru/vision available 状态。"""
    return {
        "ocr": {
            "available": bool(settings.baidu_ocr_api_key and settings.baidu_ocr_secret_key)
        },
        "mineru": {"available": check_available()},
        "vision": {"available": bool(settings.llm_api_key)},
    }


# ---------------- /api/grading ----------------


@grading_router.post("/run")
@require_permission("grading", "create")
def grading_run_endpoint(
    request: Request,
    payload: GradingRunRequest,
    db: Session = Depends(get_db),
) -> dict:
    """触发批改：无 done 任务 404；逐题批改写入 task.result['grading']。"""
    session = batch.get_session_or_404(
        db, payload.batch_id, school_id=_tenant_school_id(request.state.user)
    )
    _require_exam_in_teacher_school(db, request.state.user, payload.exam_id)
    return batch.run_grading(
        db,
        session,
        exam_id=payload.exam_id,
        teacher_answers=payload.teacher_answers,
    )


@grading_router.post("/save")
@require_permission("grading", "create")
def grading_save_endpoint(
    request: Request,
    payload: GradingSaveRequest,
    db: Session = Depends(get_db),
    client: DiagnosisLLMClient = Depends(get_diagnosis_client),
) -> dict:
    """保存结果：分档落库（ADR 0003）；模式1 落库成功后触发 run-llm 诊断（仅一次）。"""
    session = batch.get_session_or_404(
        db, payload.batch_id, school_id=_tenant_school_id(request.state.user)
    )
    _require_exam_in_teacher_school(db, request.state.user, payload.exam_id)
    school_id = _tenant_school_id(request.state.user)
    result = batch.save_grading_results(
        db, session, exam_id=payload.exam_id, school_id=school_id
    )
    run_exam_id = payload.exam_id if payload.exam_id is not None else session.exam_id
    if run_exam_id is not None and result["saved"] > 0:
        try:
            run_llm_batch(db, run_exam_id, client, request.state.user)
            result["diagnosis"] = {"status": "triggered"}
        except Exception:
            # 落库已提交，诊断失败不回滚保存结果，仅如实上报，页面可重试
            db.rollback()
            result["diagnosis"] = {"status": "failed"}
    return result


@grading_router.get("/results/{batch_id}")
@require_permission("grading", "read")
def grading_results_endpoint(
    request: Request, batch_id: int, db: Session = Depends(get_db)
) -> dict:
    """查询批改结果：逐任务 grading + 批次 submissions。"""
    return batch.results_response(
        db, batch.get_session_or_404(db, batch_id, school_id=_tenant_school_id(request.state.user))
    )
