"""OCR 批改判卷管线编排（设计 D8，仅批改判卷线）。

上传建批次 → 批次状态聚合 → 触发批改 → 分档落库（ADR 0003）→ 触发诊断。
纯逻辑 + 薄封装：API 端点只做入参解析与权限守卫；本模块只对入参做语义校验，
错误统一走 APIException 家族（detail/error_code/suggestion 三件套，9.2）。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    APIException,
    BusinessRuleViolationError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.db.models import (
    Class,
    Grade,
    Question,
    Student,
    StudentAnswer,
    StudentSubmission,
    UploadSession,
)
from app.db.models.ocr import OCRTask
from app.db.models.enums import OCRTaskStatus, UploadSessionStatus
from app.services.ocr.engines.base import UNKNOWN_STUDENT_NAME, UNKNOWN_STUDENT_NO
from app.services.ocr.grading import grade_submission
from app.services.ocr.state_machine import TERMINAL, advance_to

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".pdf"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 单文件 10MB 上限（设计 10.4）
LIST_LIMIT = 50  # 教师任务列表单页上限


# ---------------- 上传与批次 ----------------

def save_upload(file, upload_dir: str) -> str:
    """校验并保存单个上传文件：扩展名白名单 → 大小上限 → 落盘，返回绝对路径。

    大小校验改为上限读取（MAX_UPLOAD_BYTES + 1），超大文件在读取阶段即被截断，
    避免整文件 slurp 拖垮内存。
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise APIException(
            415,
            "不支持的文件格式",
            "UNSUPPORTED_FILE_FORMAT",
            "请上传 jpg/png/bmp/webp/pdf 格式的答题卡图片",
        )
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise APIException(
            413,
            "文件过大",
            "FILE_TOO_LARGE",
            f"单文件大小上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
        )
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
    path = Path(upload_dir) / f"{uuid4().hex}{ext}"
    path.write_bytes(content)
    return str(path)


def create_batch(db, files, upload_dir: str, school_id: int | None = None):
    """批量上传建批次：建 UploadSession + 逐文件 OCRTask（pending），返回 (session, task_ids)。

    空文件列表 400；任一文件校验失败（415/413）回滚并清理已落盘文件，不留半成品批次。
    school_id 为批次归属学校（教师上传时由 API 层注入，数据隔离边界）。
    """
    if not files:
        raise BusinessRuleViolationError(
            "没有上传任何文件", "NO_FILES_UPLOADED", "请选择至少一张答题卡图片"
        )
    session = UploadSession(
        status=UploadSessionStatus.uploaded,
        school_id=school_id,
    )
    db.add(session)
    db.flush()
    task_ids: list[int] = []
    written: list[str] = []
    try:
        for f in files:
            path = save_upload(f, upload_dir)
            written.append(path)
            task = OCRTask(
                session_id=session.id,
                file_path=path,
                status=OCRTaskStatus.pending,
            )
            db.add(task)
            db.flush()
            task_ids.append(task.id)
    except APIException:
        db.rollback()
        for p in written:
            Path(p).unlink(missing_ok=True)
        raise
    db.commit()
    return session, task_ids


def aggregate_tasks(db, session: UploadSession) -> dict:
    """批次状态聚合：total/done/failed/pending/processing + 逐任务明细。"""
    tasks = (
        db.query(OCRTask).filter(OCRTask.session_id == session.id).order_by(OCRTask.id).all()
    )
    counts = Counter(t.status for t in tasks)
    return {
        "batch_id": session.id,
        "status": session.status.value,
        "total": len(tasks),
        "done": counts.get(OCRTaskStatus.done, 0),
        "failed": counts.get(OCRTaskStatus.failed, 0),
        "pending": counts.get(OCRTaskStatus.pending, 0),
        "processing": counts.get(OCRTaskStatus.processing, 0),
        "tasks": [
            {
                "task_id": t.id,
                "title": f"答题卡_{i:02d}",
                "status": t.status.value,
                "progress": t.progress,
                "error": t.error,
            }
            for i, t in enumerate(tasks, 1)
        ],
    }


def teacher_batch_list(
    db, school_id: int | None = None, limit: int = LIST_LIMIT
) -> list[dict]:
    """教师任务列表：按校隔离、批次按创建时间倒序、单次查询聚齐任务明细（无 N+1）。"""
    query = db.query(UploadSession)
    if school_id is not None:
        query = query.filter(UploadSession.school_id == school_id)
    sessions = query.order_by(UploadSession.created_at.desc()).limit(limit).all()
    if not sessions:
        return []
    tasks = (
        db.query(OCRTask)
        .filter(OCRTask.session_id.in_([s.id for s in sessions]))
        .order_by(OCRTask.id)
        .all()
    )
    by_session: dict[int, list] = {}
    for t in tasks:
        by_session.setdefault(t.session_id, []).append(t)
    return [
        {
            "batch_id": s.id,
            "status": s.status.value,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "tasks": [
                {
                    "task_id": t.id,
                    "title": f"答题卡_{i:02d}",
                    "status": t.status.value,
                    "progress": t.progress,
                    "error": t.error,
                }
                for i, t in enumerate(by_session.get(s.id, []), 1)
            ],
        }
        for s in sessions
    ]


# ---------------- 触发批改 ----------------

def questions_by_position(db, exam_id: int | None) -> dict[int, Question]:
    """考试题目按 id 升序映射 1-based 位置 → Question（设计：题号即位置）。"""
    if exam_id is None:
        return {}
    questions = (
        db.query(Question)
        .filter(Question.record_id == exam_id)
        .order_by(Question.id)
        .all()
    )
    return {i + 1: q for i, q in enumerate(questions)}


def grading_bank_answers(db, exam_id: int | None) -> dict[int, str]:
    """题库标准答案：位置 → 标准答案（模式1 批改依据）。"""
    return {pos: q.answer for pos, q in questions_by_position(db, exam_id).items()}


def run_grading(
    db,
    session: UploadSession,
    *,
    exam_id: int | None = None,
    teacher_answers: dict[int, str] | None = None,
    bank_answers: dict[int, str] | None = None,
) -> dict:
    """触发批改：对批次内全部 done 任务逐题批改，结果写入 task.result['grading']。

    无 done 任务 → 404（设计 11.5）。已保存（终态 done）→ 409 防重跑覆盖已落库结果。
    首次携带 exam_id 时绑定到批次，后续 run 用不同 exam_id → 409（防 run/save 漂移）。
    会话推进到 grading（沿主链幂等）。
    """
    if session.status in TERMINAL:
        if session.status == UploadSessionStatus.done:
            raise ConflictError(
                "该批次已保存，请勿重复批改", "BATCH_ALREADY_SAVED", "刷新页面后查看已有结果"
            )
        raise ConflictError(
            "该批次已终止，无法批改", "BATCH_TERMINAL", "已废弃或出错的批次不可再批改"
        )
    if exam_id is not None:
        if session.exam_id is not None and session.exam_id != exam_id:
            raise ConflictError(
                "考试与批次已绑定不一致", "EXAM_BINDING_MISMATCH", "请使用批改时绑定的考试"
            )
        session.exam_id = exam_id
    tasks = (
        db.query(OCRTask)
        .filter(
            OCRTask.session_id == session.id,
            OCRTask.status == OCRTaskStatus.done,
        )
        .order_by(OCRTask.id)
        .all()
    )
    if not tasks:
        raise APIException(
            404,
            "没有已完成的识别任务",
            "OCR_NO_DONE_TASKS",
            "请等待识别完成后再触发批改",
        )

    bank = bank_answers if bank_answers is not None else grading_bank_answers(db, exam_id)
    has_exam = bool(bank)
    graded: list[dict] = []
    for task in tasks:
        result = dict(task.result or {})
        grading = grade_submission(
            result.get("answers", []),
            has_exam=has_exam,
            bank_answers=bank,
            teacher_answers=teacher_answers,
        )
        result["grading"] = grading
        task.result = result
        graded.append(
            {
                "task_id": task.id,
                "student_no": result.get("student_no", UNKNOWN_STUDENT_NO),
                "student_name": result.get("student_name", UNKNOWN_STUDENT_NAME),
                **grading,
            }
        )
    session.status, session.version = advance_to(
        session.status, UploadSessionStatus.grading, session.version
    )
    db.commit()
    return {
        "batch_id": session.id,
        "graded": len(graded),
        "source": graded[0]["source"] if graded else "self",
        "tasks": graded,
    }


# ---------------- 分档落库 + 触发诊断 ----------------

def _student_by_no(
    db, exam_id: int | None, school_id: int | None = None
) -> dict[str, Student]:
    if exam_id is None:
        return {}
    query = db.query(Student).filter(Student.student_no != "")
    if school_id is not None:
        query = (
            query.join(Class, Class.id == Student.class_id)
            .join(Grade, Grade.id == Class.grade_id)
            .filter(Grade.school_id == school_id)
        )
    return {s.student_no: s for s in query.all()}


def save_grading_results(
    db,
    session: UploadSession,
    *,
    exam_id: int | None = None,
    questions: dict[int, Question] | None = None,
    school_id: int | None = None,
) -> dict:
    """分档落库（ADR 0003）。模式1 展开 StudentAnswer、未注册学生跳过（11.3）；
    模式2/3 只落 StudentSubmission.answer_list（exam_id 可空）。

    前置校验：批次已保存则 409（防重复提交）；存在 done 任务缺 grading 则 400
    （防先保存后批改、防重试后漏存）。exam_id 与批次绑定（run 时写入）不一致则 409。
    保存成功后的诊断触发由 API 层接线（8.1，本函数只返回 saved 计数供触发判断）。
    """
    if session.status in TERMINAL:
        if session.status == UploadSessionStatus.done:
            raise ConflictError(
                "该批次已保存，请勿重复提交", "BATCH_ALREADY_SAVED", "刷新页面后查看已有结果"
            )
        raise ConflictError(
            "该批次已终止，无法提交", "BATCH_TERMINAL", "已废弃或出错的批次不可再保存"
        )
    if exam_id is not None and session.exam_id is not None and exam_id != session.exam_id:
        raise ConflictError(
            "提交的考试与批改时绑定不一致", "EXAM_BINDING_MISMATCH", "请使用批改时绑定的考试"
        )
    effective_exam_id = exam_id if exam_id is not None else session.exam_id
    tasks = (
        db.query(OCRTask)
        .filter(
            OCRTask.session_id == session.id,
            OCRTask.status == OCRTaskStatus.done,
        )
        .order_by(OCRTask.id)
        .all()
    )
    if any((t.result or {}).get("grading") is None for t in tasks):
        raise BusinessRuleViolationError(
            "存在尚未批改的任务", "OCR_INCOMPLETE_GRADING", "请先重新触发批改（/grading/run）再保存"
        )
    q_by_pos = questions if questions is not None else questions_by_position(db, effective_exam_id)
    students_by_no = _student_by_no(db, effective_exam_id, school_id)
    saved = skipped = submissions = 0
    for task in tasks:
        result = task.result or {}
        grading = result.get("grading")
        if grading.get("source") == "bank":
            student = students_by_no.get(result.get("student_no"))
            if student is None:
                skipped += 1  # 未注册学生静默跳过（脏数据保护）
                continue
            rows = 0
            for item in grading.get("items", []):
                q = q_by_pos.get(item.get("question_no"))
                if q is None:
                    continue
                db.add(
                    StudentAnswer(
                        student_id=student.id,
                        question_id=q.id,
                        exam_id=effective_exam_id,
                        answer_text=item.get("student_answer", ""),
                        is_correct=bool(item.get("is_correct")),
                        answered_at=datetime.utcnow(),
                    )
                )
                rows += 1
            if rows > 0:
                saved += 1  # 仅实际写入 ≥1 行才计入（避免 q_by_pos 为空时虚增触发诊断）
        else:
            items = grading.get("items", [])
            db.add(
                StudentSubmission(
                    session_id=session.id,
                    exam_id=effective_exam_id,
                    answer_list=items,
                    total_score=sum(1 for it in items if it.get("is_correct")),
                )
            )
            submissions += 1
    session.status, session.version = advance_to(
        session.status, UploadSessionStatus.done, session.version
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError(
            "保存失败：考试数据已变更", "SAVE_INTEGRITY_FAILED", "请核对考试状态后重试"
        ) from None

    return {
        "batch_id": session.id,
        "saved": saved,
        "skipped": skipped,
        "submissions": submissions,
        "status": session.status.value,
    }


def results_response(db, session: UploadSession) -> dict:
    """查询批改结果：逐任务 grading + 批次 submissions。"""
    tasks = (
        db.query(OCRTask).filter(OCRTask.session_id == session.id).order_by(OCRTask.id).all()
    )
    subs = (
        db.query(StudentSubmission)
        .filter(StudentSubmission.session_id == session.id)
        .all()
    )
    return {
        "batch_id": session.id,
        "status": session.status.value,
        "tasks": [
            {
                "task_id": t.id,
                "student_no": (t.result or {}).get("student_no", UNKNOWN_STUDENT_NO),
                "student_name": (t.result or {}).get("student_name", UNKNOWN_STUDENT_NAME),
                "status": t.status.value,
                "grading": (t.result or {}).get("grading"),
            }
            for t in tasks
        ],
        "submissions": [
            {
                "submission_id": s.id,
                "exam_id": s.exam_id,
                "total_score": s.total_score,
                "answer_list": s.answer_list,
            }
            for s in subs
        ],
    }


def get_session_or_404(
    db, session_id: int, school_id: int | None = None
) -> UploadSession:
    """取批次：未找到 404；教师按校隔离，跨校批次一律 403（约定同 _require_student_in_teacher_school）。"""
    session = db.get(UploadSession, session_id)
    if session is None:
        raise NotFoundError()
    if school_id is not None and session.school_id != school_id:
        raise ForbiddenError(
            "无权访问该批次", "BATCH_SCOPE_MISMATCH", "仅可访问本校答题卡批次"
        )
    return session
