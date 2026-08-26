"""练习 API（doc 28 §7，design.md D1/D3，挂载于 /api/practice）。

- GET  /student/{uid}/tasks   练习任务列表（pending/completed 分组 + 计数，student 仅自身）
- POST /submit                提交批改：归属校验 → 逐题判定落 StudentAnswer → 后台副作用
- GET  /effect/{student_id}   最近两次练习正确率对比与进步率
"""
import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import require_permission
from app.db.models import Account, ExamRecord, ExamType, Question, Student, StudentAnswer
from app.db.session import SessionLocal, get_db
from app.services.exercise.side_effects import get_diagnosis_client, run_submit_side_effects
from app.services.question.serializers import split_knowledge_points

practice_router = APIRouter()


def get_session_factory():
    """后台副作用会话工厂（测试可 override 注入内存库 factory）。"""
    yield SessionLocal


def get_side_effect_client():
    """后台诊断 LLM 客户端（测试可 override 注入 Mock）。"""
    return get_diagnosis_client()


def _role_id(db: Session, user) -> int:
    """把 account.id 解析为业务实体 id（student.id）。"""
    account = db.get(Account, user.user_id)
    return account.role_id if account else user.user_id


def _exam_questions(db: Session, exam: ExamRecord) -> list[Question]:
    """练习记录题目：record_id 关联的副本，或训练模式引用的原题 id。"""
    qs = [q for q in exam.questions]
    if qs:
        return qs
    refs = (exam.question_stats or {}).get("question_ids") or []
    if refs:
        return [q for q in db.query(Question).filter(Question.id.in_(refs)).all() if q]
    return []


def _practice_records(db: Session, student_id: int) -> list[ExamRecord]:
    return (
        db.query(ExamRecord)
        .filter(ExamRecord.student_id == student_id, ExamRecord.exam_type == ExamType.practice)
        .order_by(ExamRecord.exam_date.desc(), ExamRecord.id.desc())
        .all()
    )


# ---------------- 5.1 任务列表 ----------------

@practice_router.get("/student/{uid}/tasks")
@require_permission("practice", "read")
def practice_tasks(request: Request, uid: int, db: Session = Depends(get_db)) -> dict:
    user = request.state.user
    if user.role == "student" and _role_id(db, user) != uid:
        raise ForbiddenError()  # 学生仅可查自身任务
    today = datetime.date.today()
    tasks: list[dict] = []
    for exam in _practice_records(db, uid):
        questions = _exam_questions(db, exam)
        kps = []
        for q in questions:
            kps += split_knowledge_points(q.knowledge_points)
        question_stats = exam.question_stats or {}
        deadline_raw = question_stats.get("deadline")
        deadline = (
            datetime.date.fromisoformat(deadline_raw) if deadline_raw else None
        )
        answered = (
            db.query(StudentAnswer.id)
            .filter(StudentAnswer.student_id == uid, StudentAnswer.exam_id == exam.id)
            .first()
        )
        if answered is not None:
            status = "completed"
        elif deadline is not None and deadline < today:
            status = "expired"
        else:
            status = "pending"
        tasks.append({
            "practice_id": exam.id,
            "title": exam.name,
            "knowledge_points": list(dict.fromkeys(kps)),
            "difficulty": question_stats.get("difficulty") or (questions[0].difficulty.value if questions and questions[0].difficulty else "medium"),
            "status": status,
            "question_count": len(questions),
            "deadline": deadline.isoformat() if deadline else None,
        })
    return {
        "student_id": uid,
        "pending_count": sum(1 for t in tasks if t["status"] == "pending"),
        "completed_count": sum(1 for t in tasks if t["status"] == "completed"),
        "tasks": tasks,
    }


# ---------------- 5.2 提交批改 ----------------

class SubmitAnswer(BaseModel):
    question_id: int
    selected_option: str = Field(..., max_length=2000)


class SubmitRequest(BaseModel):
    practice_id: int
    answers: list[SubmitAnswer] = Field(..., min_length=1)


@practice_router.post("/submit")
@require_permission("practice", "create")
def submit(
    request: Request,
    payload: SubmitRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
    side_client=Depends(get_side_effect_client),
) -> dict:
    """归属校验 → 逐题判定落 StudentAnswer（含 answered_at）→ 后台副作用不阻塞。"""
    requester_id = _role_id(db, request.state.user)
    exam = db.get(ExamRecord, payload.practice_id)
    if exam is None:
        raise NotFoundError(detail="练习记录不存在", error_code="PRACTICE_NOT_FOUND")
    if exam.student_id != requester_id:
        raise ForbiddenError(detail="非本人练习，无法提交", error_code="PRACTICE_NOT_OWNED")

    # 先校验全部题目存在（他人/缺失均不落库）
    qids = [a.question_id for a in payload.answers]
    questions = {q.id: q for q in db.query(Question).filter(Question.id.in_(qids)).all()}
    for qid in qids:
        if qid not in questions:
            raise NotFoundError(detail=f"题目不存在：{qid}", error_code="QUESTION_NOT_FOUND")

    now = datetime.datetime.utcnow()
    results: list[dict] = []
    wrong_ids: list[int] = []
    for ans in payload.answers:
        q = questions[ans.question_id]
        is_correct = bool(q.answer.strip().upper() == ans.selected_option.strip().upper())
        if not is_correct:
            wrong_ids.append(q.id)
        db.add(StudentAnswer(
            student_id=requester_id,
            question_id=q.id,
            exam_id=exam.id,
            answer_text=ans.selected_option,
            is_correct=is_correct,
            answered_at=now,
        ))
        results.append({
            "question_id": q.id,
            "is_correct": is_correct,
            "correct_answer": q.answer,
            "analysis": q.analysis or "",
        })
    db.commit()

    background.add_task(run_submit_side_effects, session_factory, requester_id, wrong_ids, side_client)
    total = len(results)
    score = sum(1 for r in results if r["is_correct"])
    return {
        "practice_id": exam.id,
        "score": score,
        "total": total,
        "accuracy": round(score / total, 4) if total else 0.0,
        "results": results,
    }


# ---------------- 5.3 效果追踪 ----------------

@practice_router.get("/effect/{student_id}")
@require_permission("practice", "read")
def effect(request: Request, student_id: int, db: Session = Depends(get_db)) -> dict:
    user = request.state.user
    if user.role == "student" and _role_id(db, user) != student_id:
        raise ForbiddenError()
    student = db.get(Student, student_id)
    name = student.name if student else ""
    records: list[dict] = []
    for exam in _practice_records(db, student_id):
        answers = (
            db.query(StudentAnswer)
            .filter(StudentAnswer.student_id == student_id, StudentAnswer.exam_id == exam.id)
            .all()
        )
        if not answers:
            continue
        records.append({
            "date": exam.exam_date.isoformat(),
            "accuracy": round(
                sum(1 for a in answers if a.is_correct) / len(answers), 4
            ),
        })
    recent = records[:2]
    improvement = None
    if len(recent) >= 2:
        prev, cur = recent[1], recent[0]
        improvement = {
            "previous": {"date": prev["date"], "accuracy": prev["accuracy"]},
            "current": {"date": cur["date"], "accuracy": cur["accuracy"]},
            "improvement": round(cur["accuracy"] - prev["accuracy"], 4),
        }
    return {
        "student_id": student_id,
        "student_name": name,
        "improvement": improvement,
    }
