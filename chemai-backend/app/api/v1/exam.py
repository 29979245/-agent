"""考试生命周期 API（exam-lifecycle-api，doc 47）。

挂载于 /api/exam，全部 teacher+ 权限（resource=exam）：
- 写（创建/关联/发布/阅卷/完成/归档）：exam:create / exam:update
- 读（题目/结果）：exam:read（student 仅可读）
- 删除草稿：exam:delete（teacher+ 有）
"""
import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.practice import (
    SubmitAnswer,
    get_session_factory,
    get_side_effect_client,
)
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.permissions import permission_checker, require_permission
from app.db.models import (
    Account,
    Class,
    ExamRecord,
    Grade,
    Question,
    Student,
    StudentAnswer,
    Teacher,
)
from app.db.models.enums import ExamStatus, ExamType
from app.db.session import get_db
from app.services.diagnosis.aggregation import refresh_profiles
from app.services.exercise.side_effects import run_submit_side_effects
from app.services.ocr.grading import grade_subjective
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


class ReviewAnswerRequest(BaseModel):
    is_correct: bool
    comment: str = Field(default="", max_length=500)


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


def _role_id(db: Session, user) -> int:
    """把 account.id 解析为业务实体 id（teacher.id / student.id）。"""
    account = db.get(Account, user.user_id)
    return account.role_id if account else user.user_id


def _ensure_student(user) -> None:
    """学生专属端点守卫：非学生角色直接 403（panel _ensure_not_student 的反向惯例）。"""
    if user.role != "student":
        raise ForbiddenError(detail="仅学生可操作", error_code="STUDENT_ONLY")


def _require_teacher_exam_school(db: Session, request: Request, exam) -> None:
    """复核端点守卫：学生拒绝；教师仅本校（组织链隔离，admin 不限；class_id 为空跳过隔离）。"""
    if request.state.user.role == "student":
        raise ForbiddenError()
    if request.state.user.role != "teacher" or exam.class_id is None:
        return
    cls = db.get(Class, exam.class_id)
    if cls is None:
        raise NotFoundError()
    grade = db.get(Grade, cls.grade_id)
    teacher = db.get(Teacher, _role_id(db, request.state.user))
    if grade is None or teacher is None or grade.school_id != teacher.school_id:
        raise ForbiddenError()


class ExamSubmitRequest(BaseModel):
    answers: list[SubmitAnswer] = Field(..., min_length=1)


def _student_cannot_access(db: Session, user, target_student_id: int) -> bool:
    """student 仅可读自身作答（组织链隔离；跨学生访问即 403）。"""
    return user.role == "student" and _role_id(db, user) != target_student_id


@exam_router.get("")
@require_permission("exam", "read")
def list_exams(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    return ExamService(db).list_exams(page=page, page_size=page_size)


@exam_router.get("/mine")
@require_permission("exam", "read")
def my_exams(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """学生自己的班级考试列表：本班 published/in_progress（待作答）+ 该生已提交（含 completed）。

    已完成但该生未参加的考试不展示（未参加即无作答记录）。
    """
    _ensure_student(request.state.user)
    sid = _role_id(db, request.state.user)
    student = db.get(Student, sid)
    if student is None:
        raise NotFoundError(detail="学生不存在", error_code="STUDENT_NOT_FOUND")
    rows = (
        db.query(ExamRecord)
        .filter(
            ExamRecord.class_id == student.class_id,
            ExamRecord.status.in_(
                (ExamStatus.published, ExamStatus.in_progress, ExamStatus.completed)
            ),
        )
        .order_by(ExamRecord.id.desc())
        .all()
    )
    items = []
    for e in rows:
        submitted = (
            db.query(StudentAnswer).filter_by(exam_id=e.id, student_id=sid).count() > 0
        )
        if e.status == ExamStatus.completed and not submitted:
            continue
        items.append(
            {
                "exam_id": e.id,
                "name": e.name,
                "exam_date": e.exam_date.isoformat() if e.exam_date else None,
                "question_count": db.query(Question)
                .filter(Question.record_id == e.id)
                .count(),
                "submitted": submitted,
                "status": e.status.value,
            }
        )
    return {"items": items}


@classes_router.get("/classes")
@require_permission("exam", "read")
def list_classes(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """班级列表：教师角色仅返回本校班级（Class→Grade→school_id 隔离），其余角色全量。"""
    query = db.query(Class)
    if request.state.user.role == "teacher":
        teacher = db.get(Teacher, _role_id(db, request.state.user))
        if teacher is None:
            return {"items": []}
        query = (
            query.join(Grade, Class.grade_id == Grade.id)
            .filter(Grade.school_id == teacher.school_id)
        )
    rows = query.order_by(Class.id).all()
    return {
        "items": [{"id": c.id, "name": c.name, "grade_id": c.grade_id} for c in rows]
    }


@classes_router.get("/classes/{class_id}/students")
@require_permission("analysis", "read")
def list_class_students(
    request: Request, class_id: int, db: Session = Depends(get_db)
) -> dict:
    """班级学生列表（面板渲染 KPI 与重点关注横条）；教师仅本校、student 拒绝。"""
    from app.api.v1.panel import _ensure_not_student, _require_class_in_teacher_school
    from app.services.analytics.panel_service import class_students

    _ensure_not_student(request)
    _require_class_in_teacher_school(db, request, class_id)
    return class_students(db, class_id)


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
    # 学生读卷不得携带答案与解析（防止整卷答案泄漏）；教师保留完整题目
    include_answer = request.state.user.role != "student"
    if not include_answer:
        # 班级考试（class_id 非空）校验学生归属；练习（class_id=None）不受影响
        exam = db.get(ExamRecord, exam_id)
        student = db.get(Student, _role_id(db, request.state.user))
        if (
            exam is not None
            and exam.class_id is not None
            and student is not None
            and exam.class_id != student.class_id
        ):
            raise ForbiddenError(detail="非本班考试", error_code="EXAM_NOT_IN_CLASS")
    return {
        "exam_id": exam_id,
        "items": ExamService(db).list_questions(exam_id, include_answer=include_answer),
    }


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


@exam_router.post("/{exam_id}/submit")
async def submit_exam(
    request: Request,
    exam_id: int,
    payload: ExamSubmitRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
    side_client=Depends(get_side_effect_client),
) -> dict:
    """学生提交班级考试作答：本班归属 → 考试未结束 → 未重复 → 逐题判分落库。

    判分：选择题（有 options）确定性字符串比对；非选择题走 LLM 语义批改
    （grade_subjective，容错标 review_needed 人工复核）。首次提交把考试
    published→in_progress（幂等）。提交副作用（复习同步+诊断+画像聚合）异步执行。
    """
    request.state.user = permission_checker.check_from_request(request, "exam", "read")
    _ensure_student(request.state.user)
    sid = _role_id(db, request.state.user)
    student = db.get(Student, sid)
    if student is None:
        raise NotFoundError(detail="学生不存在", error_code="STUDENT_NOT_FOUND")
    exam = db.get(ExamRecord, exam_id)
    if exam is None:
        raise NotFoundError(detail="考试不存在", error_code="EXAM_NOT_FOUND")
    if exam.class_id != student.class_id:
        raise ForbiddenError(detail="非本班考试", error_code="EXAM_NOT_IN_CLASS")
    if exam.status not in (ExamStatus.published, ExamStatus.in_progress):
        raise ConflictError(
            detail="考试已结束或阅卷中，不可在线提交",
            error_code="EXAM_NOT_OPEN",
            suggestion="如考试已阅卷/完成，请通过教师端查看结果",
        )
    if (
        db.query(StudentAnswer).filter_by(exam_id=exam_id, student_id=sid).first()
        is not None
    ):
        raise ConflictError(
            detail="该考试已提交，不能重复作答",
            error_code="EXAM_ALREADY_SUBMITTED",
        )

    qids = [a.question_id for a in payload.answers]
    questions = {
        q.id: q for q in db.query(Question).filter(Question.id.in_(qids)).all()
    }
    for qid in qids:
        if qid not in questions:
            raise NotFoundError(detail=f"题目不存在：{qid}", error_code="QUESTION_NOT_FOUND")

    now = datetime.datetime.utcnow()
    wrong_ids: list[int] = []
    results: list[dict] = []
    for ans in payload.answers:
        q = questions[ans.question_id]
        if q.options:
            is_correct = bool(
                q.answer.strip().upper() == ans.selected_option.strip().upper()
            )
            review_needed, reason = False, ""
        else:
            subj = await grade_subjective(
                q.content, ans.selected_option, q.answer, client=side_client
            )
            is_correct, review_needed, reason = (
                subj["is_correct"],
                subj["review_needed"],
                subj["reason"],
            )
        if not is_correct:
            wrong_ids.append(q.id)
        db.add(
            StudentAnswer(
                student_id=sid,
                question_id=q.id,
                exam_id=exam.id,
                answer_text=ans.selected_option,
                is_correct=is_correct,
                answered_at=now,
                review_needed=review_needed,
                review_reason=reason,
            )
        )
        results.append(
            {
                "question_id": q.id,
                "is_correct": is_correct,
                "correct_answer": q.answer,
                "analysis": q.analysis or "",
                "review_needed": review_needed,
                "reason": reason,
            }
        )

    if exam.status == ExamStatus.published:  # 首生作答 → in_progress（幂等）
        exam.status = ExamStatus.in_progress
    db.commit()

    background.add_task(
        run_submit_side_effects, session_factory, sid, wrong_ids, side_client
    )
    total = len(results)
    score = sum(1 for r in results if r["is_correct"])
    return {
        "exam_id": exam.id,
        "score": score,
        "total": total,
        "accuracy": round(score / total, 4) if total else 0.0,
        "results": results,
    }


@exam_router.post("/{exam_id}/finalize")
@require_permission("exam", "update")
def finalize_exam(
    request: Request,
    exam_id: int,
    db: Session = Depends(get_db),
) -> dict:
    exam = ExamService(db).finalize(exam_id)
    db.commit()
    # 完成统计 → 兜底聚合该考试参与学生画像（提交副作用已聚合一次，此处兜底）
    attendees = [
        a.student_id
        for a in db.query(StudentAnswer).filter_by(exam_id=exam_id).all()
    ]
    if attendees:
        refresh_profiles(db, list(dict.fromkeys(attendees)))
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
    if request.state.user.role == "student":
        raise ForbiddenError()  # 全班成绩总览仅教师+
    return ExamService(db).results(exam_id)


@exam_router.get("/{exam_id}/result/{student_id}")
@require_permission("exam", "read")
def student_result(
    request: Request,
    exam_id: int,
    student_id: int,
    db: Session = Depends(get_db),
) -> dict:
    if _student_cannot_access(db, request.state.user, student_id):
        raise ForbiddenError()  # 学生仅可读自身作答
    return ExamService(db).student_result(exam_id, student_id)


@exam_router.get("/{exam_id}/reviews")
@require_permission("exam", "read")
def exam_reviews(
    request: Request,
    exam_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """待人工复核清单：LLM 判不出的主观题作答（教师+，学校隔离）。"""
    exam = ExamService(db)._get(exam_id)
    _require_teacher_exam_school(db, request, exam)
    return ExamService(db).review_queue(exam_id)


@exam_router.post("/{exam_id}/answers/{answer_id}/review")
@require_permission("exam", "update")
def review_exam_answer(
    request: Request,
    exam_id: int,
    answer_id: int,
    payload: ReviewAnswerRequest,
    db: Session = Depends(get_db),
) -> dict:
    """教师逐题改判：落 is_correct/备注，清 review_needed。"""
    exam = ExamService(db)._get(exam_id)
    _require_teacher_exam_school(db, request, exam)
    result = ExamService(db).review_answer(
        exam_id, answer_id, payload.is_correct, payload.comment
    )
    db.commit()
    return result


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
