"""OCR 批改判卷管线编排（设计 D8，仅批改判卷线）。

上传建批次 → 批次状态聚合 → 触发批改 → 分档落库（ADR 0003）→ 触发诊断。
纯逻辑 + 薄封装：API 端点只做入参解析与权限守卫。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.core.exceptions import NotFoundError
from app.db.models import (
    Question,
    Student,
    StudentAnswer,
    StudentSubmission,
    UploadSession,
)
from app.db.models.ocr import OCRTask
from app.db.models.enums import OCRTaskStatus, UploadSessionStatus
from app.services.ocr.grading import grade_submission
from app.services.ocr.state_machine import advance_to

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".pdf"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 单文件 10MB 上限（设计 10.4）


# ---------------- 上传与批次 ----------------

def save_upload(file, upload_dir: str) -> str:
    """校验并保存单个上传文件：扩展名白名单 → 大小上限 → 落盘，返回绝对路径。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Unsupported file format")
    content = file.file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    Path(upload_dir).mkdir(parents=True, exist_ok=True)
    path = Path(upload_dir) / f"{uuid4().hex}{ext}"
    path.write_bytes(content)
    return str(path)


def create_batch(db, files, upload_dir: str, exam_id: int | None = None):
    """批量上传建批次：建 UploadSession + 逐文件 OCRTask（pending），返回 (session, task_ids)。

    空文件列表 400；任一文件校验失败（415/413）回滚，不留半成品批次。
    """
    if not files:
        raise HTTPException(400, "No files uploaded")
    session = UploadSession(status=UploadSessionStatus.uploaded)
    db.add(session)
    db.flush()
    task_ids: list[int] = []
    try:
        for f in files:
            task = OCRTask(
                session_id=session.id,
                file_path=save_upload(f, upload_dir),
                status=OCRTaskStatus.pending,
            )
            db.add(task)
            db.flush()
            task_ids.append(task.id)
    except HTTPException:
        db.rollback()
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


def teacher_batch_list(db) -> list[dict]:
    """教师任务列表：全批次按创建时间倒序，含各批次任务明细。"""
    sessions = (
        db.query(UploadSession).order_by(UploadSession.created_at.desc()).all()
    )
    return [
        {
            "batch_id": s.id,
            "status": s.status.value,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "tasks": aggregate_tasks(db, s)["tasks"],
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

    无 done 任务 → 404（设计 11.5）。会话推进到 grading（沿主链幂等）。
    """
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
        raise HTTPException(404, "No completed OCR tasks found")

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
                "student_no": result.get("student_no", "unknown"),
                "student_name": result.get("student_name", "待识别"),
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

def _student_by_no(db, exam_id: int | None) -> dict[str, Student]:
    if exam_id is None:
        return {}
    return {
        s.student_no: s
        for s in db.query(Student).filter(Student.student_no != "").all()
    }


def save_grading_results(
    db,
    session: UploadSession,
    *,
    exam_id: int | None = None,
    questions: dict[int, Question] | None = None,
) -> dict:
    """分档落库（ADR 0003）。模式1 展开 StudentAnswer、未注册学生跳过（11.3）；
    模式2/3 只落 StudentSubmission.answer_list（exam_id 可空）。

    保存成功后的诊断触发由 API 层接线（8.1，本函数只返回 saved 计数供触发判断）。
    """
    tasks = (
        db.query(OCRTask)
        .filter(
            OCRTask.session_id == session.id,
            OCRTask.status == OCRTaskStatus.done,
        )
        .order_by(OCRTask.id)
        .all()
    )
    q_by_pos = questions if questions is not None else questions_by_position(db, exam_id)
    students_by_no = _student_by_no(db, exam_id)
    saved = skipped = submissions = 0
    for task in tasks:
        result = task.result or {}
        grading = result.get("grading")
        if not grading:
            continue
        if grading.get("source") == "bank":
            student = students_by_no.get(result.get("student_no"))
            if student is None:
                skipped += 1  # 未注册学生静默跳过（脏数据保护）
                continue
            for item in grading.get("items", []):
                q = q_by_pos.get(item.get("question_no"))
                if q is None:
                    continue
                db.add(
                    StudentAnswer(
                        student_id=student.id,
                        question_id=q.id,
                        exam_id=exam_id,
                        answer_text=item.get("student_answer", ""),
                        is_correct=bool(item.get("is_correct")),
                        answered_at=datetime.utcnow(),
                    )
                )
            saved += 1
        else:
            items = grading.get("items", [])
            db.add(
                StudentSubmission(
                    session_id=session.id,
                    exam_id=exam_id,
                    answer_list=items,
                    total_score=sum(1 for it in items if it.get("is_correct")),
                )
            )
            submissions += 1
    session.status, session.version = advance_to(
        session.status, UploadSessionStatus.done, session.version
    )
    db.commit()

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
                "student_no": (t.result or {}).get("student_no", "unknown"),
                "student_name": (t.result or {}).get("student_name", "待识别"),
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


def get_session_or_404(db, session_id: int) -> UploadSession:
    session = db.get(UploadSession, session_id)
    if session is None:
        raise NotFoundError()
    return session
