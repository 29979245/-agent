"""间隔复习 API（doc 29 §4-§6，design.md D4/D5，挂载于 /api/review）。

- GET  /tasks/{student_id}   到期任务列表（pending/overdue，按到期升序，student 仅自身）
- POST /submit               提交判级：归属校验 → 升降级状态机 → 只写 ReviewHistory
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import require_permission
from app.db.models import Account, Question, ReviewTask
from app.db.session import get_db
from app.services.exercise.spaced_repetition import SpacedRepetitionEngine
from app.services.question.serializers import split_knowledge_points

review_router = APIRouter()


def _role_id(db: Session, user) -> int:
    account = db.get(Account, user.user_id)
    return account.role_id if account else user.user_id


# ---------------- 6.1 到期查询 ----------------

@review_router.get("/tasks/{student_id}")
@require_permission("practice", "read")
def review_tasks(request: Request, student_id: int, db: Session = Depends(get_db)) -> dict:
    user = request.state.user
    if user.role == "student" and _role_id(db, user) != student_id:
        raise ForbiddenError()  # 学生仅可查自身
    tasks = SpacedRepetitionEngine(db).list_due_tasks(student_id)
    items = []
    for task in tasks:
        q = db.get(Question, task.question_id)
        items.append({
            "review_task_id": task.id,
            "question_id": task.question_id,
            "content": q.content if q else "",
            "options": q.options if q else [],
            "knowledge_points": split_knowledge_points(q.knowledge_points) if q else [],
            "review_level": task.review_level.value,
            "status": task.status.value,
            "next_review_at": task.next_review_at.isoformat() if task.next_review_at else None,
        })
    return {"student_id": student_id, "count": len(items), "tasks": items}


# ---------------- 6.2 提交判级 ----------------

class ReviewSubmitRequest(BaseModel):
    review_task_id: int
    passed: bool


@review_router.post("/submit")
@require_permission("practice", "update")
def submit(
    request: Request,
    payload: ReviewSubmitRequest,
    db: Session = Depends(get_db),
) -> dict:
    requester_id = _role_id(db, request.state.user)
    task = db.get(ReviewTask, payload.review_task_id)
    if task is None:
        raise NotFoundError(detail="复习任务不存在", error_code="REVIEW_TASK_NOT_FOUND")
    if task.student_id != requester_id:
        raise ForbiddenError(detail="非本人复习任务", error_code="REVIEW_TASK_NOT_OWNED")
    engine = SpacedRepetitionEngine(db)
    updated = engine.apply_review(task, passed=payload.passed)
    db.commit()
    return {
        "review_task_id": updated.id,
        "question_id": updated.question_id,
        "review_level": updated.review_level.value,
        "status": updated.status.value,
        "next_review_at": updated.next_review_at.isoformat() if updated.next_review_at else None,
        "consecutive_correct": updated.consecutive_correct,
        "consecutive_error": updated.consecutive_error,
        "passed": payload.passed,
    }
