"""错题强化 API（doc 29 §5-§8，design.md D1/D2，挂载于 /api/wrong-questions）。

- GET  /{student_id}                    错题列表（student 仅自身）+ 错题统计
- POST /variants                        变式题生成（同知识点同难度抽样，排除原题）
- POST /train                           训练会话（per-student 训练记录）
- POST /{question_id}/mastered          标记已掌握（非本人 403）
"""
import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError
from app.core.permissions import require_permission
from app.db.models import Account, ReviewTask
from app.db.models.enums import ReviewTaskStatus
from app.db.session import get_db
from app.services.exercise.wrong_question import WrongQuestionTrainer

wrong_question_router = APIRouter()


def _role_id(db: Session, user) -> int:
    account = db.get(Account, user.user_id)
    return account.role_id if account else user.user_id


def _require_own(db: Session, user, student_id: int) -> None:
    if user.role == "student" and _role_id(db, user) != student_id:
        raise ForbiddenError()


# ---------------- 7.1 错题列表 ----------------

@wrong_question_router.get("/{student_id}")
@require_permission("practice", "read")
def wrong_list(request: Request, student_id: int, db: Session = Depends(get_db)) -> dict:
    _require_own(db, request.state.user, student_id)
    items = WrongQuestionTrainer(db).list_wrong_questions(student_id)
    mastered = (
        db.query(ReviewTask)
        .filter(ReviewTask.student_id == student_id, ReviewTask.status == ReviewTaskStatus.done)
        .count()
    )
    week_start = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    week_new = 0
    for it in items:
        ts = it.get("last_answered_at")
        if ts:
            try:
                if datetime.datetime.fromisoformat(ts) >= week_start:
                    week_new += 1
            except ValueError:
                pass
    return {
        "student_id": student_id,
        "count": len(items),
        "stats": {"total": len(items), "week_new": week_new, "mastered": mastered},
        "items": items,
    }


# ---------------- 7.2 变式题 ----------------

class VariantRequest(BaseModel):
    question_id: int
    count: int = Field(default=1, ge=1, le=10)


@wrong_question_router.post("/variants")
@require_permission("practice", "create")
def variants(
    request: Request,
    payload: VariantRequest,
    db: Session = Depends(get_db),
) -> dict:
    student_id = _role_id(db, request.state.user)
    return WrongQuestionTrainer(db).generate_variants(student_id, payload.question_id, payload.count)


# ---------------- 7.3 训练会话 ----------------

class TrainAnswer(BaseModel):
    question_id: int
    selected_option: str = Field(..., max_length=2000)


class TrainRequest(BaseModel):
    student_id: int
    answers: list[TrainAnswer] = Field(..., min_length=1)


@wrong_question_router.post("/train")
@require_permission("practice", "create")
def train(
    request: Request,
    payload: TrainRequest,
    db: Session = Depends(get_db),
) -> dict:
    _require_own(db, request.state.user, payload.student_id)
    return WrongQuestionTrainer(db).start_training(
        payload.student_id,
        [{"question_id": a.question_id, "selected_option": a.selected_option} for a in payload.answers],
    )


# ---------------- 7.4 标记已掌握 ----------------

class MasteredRequest(BaseModel):
    student_id: int


@wrong_question_router.post("/{question_id}/mastered")
@require_permission("practice", "update")
def mastered(
    request: Request,
    question_id: int,
    payload: MasteredRequest,
    db: Session = Depends(get_db),
) -> dict:
    _require_own(db, request.state.user, payload.student_id)
    return WrongQuestionTrainer(db).mark_mastered(payload.student_id, question_id)
