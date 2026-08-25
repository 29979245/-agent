"""考试生命周期 API（exam-lifecycle-api，doc 47）。

挂载于 /api/exam，全部 teacher+ 权限（resource=exam）：
- 写（创建/关联/发布/阅卷/完成/归档）：exam:create / exam:update
- 读（题目/结果）：exam:read（student 仅可读）
- 删除草稿：exam:delete（teacher+ 有）
"""
import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.permissions import require_permission
from app.db.models import Class
from app.db.models.enums import ExamType
from app.db.session import get_db
from app.services.question.exam_service import ExamService

exam_router = APIRouter()
classes_router = APIRouter()


class CreateExamRequest(BaseModel):
    class_id: int
    name: str = ""
    exam_date: str = ""
    exam_type: str = "exam"


class AddQuestionsRequest(BaseModel):
    question_ids: list[int | str] = Field(default_factory=list)


def _parse_exam_type(raw: str) -> ExamType:
    try:
        return ExamType(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效考试类型：{raw}")


def _parse_date(raw: str) -> datetime.date:
    if not raw:
        return datetime.date.today()
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效日期：{raw}")


@exam_router.get("")
@require_permission("exam", "read")
def list_exams(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    return ExamService(db).list_exams(page=page, page_size=page_size)


@classes_router.get("/classes")
@require_permission("exam", "read")
def list_classes(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    rows = db.query(Class).order_by(Class.id).all()
    return {"items": [{"id": c.id, "name": c.name} for c in rows]}


@exam_router.post("/create")
@require_permission("exam", "create")
def create_exam(
    request: Request,
    payload: CreateExamRequest,
    db: Session = Depends(get_db),
) -> dict:
    exam = ExamService(db).create(
        class_id=payload.class_id,
        name=payload.name,
        exam_date=_parse_date(payload.exam_date),
        exam_type=_parse_exam_type(payload.exam_type),
    )
    db.commit()
    return {"exam_id": exam.id, "status": exam.status.value}


@exam_router.post("/{exam_id}/questions")
@require_permission("exam", "update")
def add_questions(
    request: Request,
    exam_id: int,
    payload: AddQuestionsRequest,
    db: Session = Depends(get_db),
) -> dict:
    result = ExamService(db).add_questions(exam_id, payload.question_ids)
    db.commit()
    return {"exam_id": exam_id, **result}


@exam_router.get("/{exam_id}/questions")
@require_permission("exam", "read")
def list_questions(
    request: Request,
    exam_id: int,
    db: Session = Depends(get_db),
) -> dict:
    return {"exam_id": exam_id, "items": ExamService(db).list_questions(exam_id)}


@exam_router.delete("/{exam_id}/questions/{question_id}")
@require_permission("exam", "update")
def remove_question(
    request: Request,
    exam_id: int,
    question_id: int,
    db: Session = Depends(get_db),
) -> dict:
    ExamService(db).remove_question(exam_id, question_id)
    db.commit()
    return {"removed": question_id}


@exam_router.post("/{exam_id}/publish")
@require_permission("exam", "update")
def publish_exam(
    request: Request,
    exam_id: int,
    db: Session = Depends(get_db),
) -> dict:
    exam = ExamService(db).publish(exam_id)
    db.commit()
    return {"exam_id": exam.id, "status": exam.status.value, "question_stats": exam.question_stats}


@exam_router.post("/{exam_id}/start-grading")
@require_permission("exam", "update")
def start_grading(
    request: Request,
    exam_id: int,
    db: Session = Depends(get_db),
) -> dict:
    exam = ExamService(db).start_grading(exam_id)
    db.commit()
    return {"exam_id": exam.id, "status": exam.status.value}


@exam_router.post("/{exam_id}/finalize")
@require_permission("exam", "update")
def finalize_exam(
    request: Request,
    exam_id: int,
    db: Session = Depends(get_db),
) -> dict:
    exam = ExamService(db).finalize(exam_id)
    db.commit()
    return {"exam_id": exam.id, "status": exam.status.value, "stats": exam.stats}


@exam_router.post("/{exam_id}/archive")
@require_permission("exam", "update")
def archive_exam(
    request: Request,
    exam_id: int,
    db: Session = Depends(get_db),
) -> dict:
    exam = ExamService(db).archive(exam_id)
    db.commit()
    return {"exam_id": exam.id, "status": exam.status.value}


@exam_router.get("/{exam_id}/results")
@require_permission("exam", "read")
def exam_results(
    request: Request,
    exam_id: int,
    db: Session = Depends(get_db),
) -> dict:
    return ExamService(db).results(exam_id)


@exam_router.get("/{exam_id}/result/{student_id}")
@require_permission("exam", "read")
def student_result(
    request: Request,
    exam_id: int,
    student_id: int,
    db: Session = Depends(get_db),
) -> dict:
    return ExamService(db).student_result(exam_id, student_id)


@exam_router.delete("/{exam_id}")
@require_permission("exam", "delete")
def delete_exam(
    request: Request,
    exam_id: int,
    db: Session = Depends(get_db),
) -> dict:
    ExamService(db).delete(exam_id)
    db.commit()
    return {"deleted": exam_id}
