"""题库管理 API（exam-bank-api，doc 47）。

挂载于 /api/exam-bank，全部 teacher+ 权限：
- 写（建夹/导入）：question:create
- 读（列表/详情/试卷树/历史真题）：question:read
- 删（删夹/移题）：question:update（矩阵无 question:delete 于 teacher，删夹仅解除关联）
"""
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.permissions import require_permission
from app.db.session import get_db
from app.services.question.exam_bank import ExamBankService
from app.services.question.historical import get_bank

exam_bank_router = APIRouter()


class CreateSetRequest(BaseModel):
    name: str = Field(..., min_length=1)
    region: str = ""
    year: int = 0
    description: str = ""


class ImportQuestionsRequest(BaseModel):
    question_ids: list[int] = Field(default_factory=list)


@exam_bank_router.post("/exam-sets")
@require_permission("question", "create")
def create_exam_set(
    request: Request,
    payload: CreateSetRequest,
    db: Session = Depends(get_db),
) -> dict:
    svc = ExamBankService(db)
    user = request.state.user
    qs = svc.create_set(
        name=payload.name,
        teacher_id=user.user_id,
        region=payload.region,
        year=payload.year,
        description=payload.description,
    )
    db.commit()
    return {"id": qs.id, "name": qs.name}


@exam_bank_router.get("/exam-sets")
@require_permission("question", "read")
def list_exam_sets(
    request: Request,
    teacher_id: int | None = Query(default=None),
    region: str | None = Query(default=None),
    year: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    return ExamBankService(db).list_sets(
        teacher_id=teacher_id, region=region, year=year, page=page, page_size=page_size
    )


@exam_bank_router.get("/exam-sets/{set_id}")
@require_permission("question", "read")
def get_exam_set(
    request: Request,
    set_id: int,
    db: Session = Depends(get_db),
) -> dict:
    svc = ExamBankService(db)
    qs = svc.get_set(set_id)
    return {
        "id": qs.id,
        "name": qs.name,
        "region": qs.region,
        "year": qs.year,
        "description": qs.description,
        "question_count": qs.question_count,
        "is_preset": qs.is_preset,
        "questions": svc.get_set_questions(set_id),
    }


@exam_bank_router.delete("/exam-sets/{set_id}")
@require_permission("question", "update")
def delete_exam_set(
    request: Request,
    set_id: int,
    db: Session = Depends(get_db),
) -> dict:
    ExamBankService(db).delete_set(set_id)
    db.commit()
    return {"deleted": set_id}


@exam_bank_router.post("/exam-sets/{set_id}/import-questions")
@require_permission("question", "create")
def import_questions(
    request: Request,
    set_id: int,
    payload: ImportQuestionsRequest,
    db: Session = Depends(get_db),
) -> dict:
    result = ExamBankService(db).import_questions(set_id, payload.question_ids)
    db.commit()
    return {"set_id": set_id, **result}


@exam_bank_router.delete("/exam-sets/{set_id}/questions/{question_id}")
@require_permission("question", "update")
def remove_question(
    request: Request,
    set_id: int,
    question_id: int,
    db: Session = Depends(get_db),
) -> dict:
    ExamBankService(db).remove_question(set_id, question_id)
    db.commit()
    return {"removed": question_id}


@exam_bank_router.get("/papers")
@require_permission("question", "read")
def paper_tree(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    return {"tree": get_bank().paper_tree()}


@exam_bank_router.get("/historical")
@require_permission("question", "read")
def historical_search(
    request: Request,
    keyword: str | None = Query(default=None),
    region: str | None = Query(default=None),
    year: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    return get_bank().search(
        keyword=keyword, region=region, year=year, page=page, page_size=page_size
    )
