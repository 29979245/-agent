"""学生个人报告聚合服务（doc 52「我的」页 / doc 40 §2.5）。

聚合策略：复用 practice.py 的练习口径（ExamRecord type=practice 排除 training/variant），
单学生作答量级小，全内存聚合不缓存；统计口径见 change student-supplement-apis design.md D5。
"""
import datetime
from collections import defaultdict

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.models import ExamRecord, ExamType, Question, Student, StudentAnswer
from app.services.question.serializers import split_knowledge_points

# training/variant 会话不算「练习」（与 practice.py _NON_PRACTICE_MODES 一致）
_NON_PRACTICE_MODES = {"training", "variant"}


def _practice_records(db: Session, student_id: int) -> list[ExamRecord]:
    rows = (
        db.query(ExamRecord)
        .filter(
            ExamRecord.student_id == student_id,
            ExamRecord.exam_type == ExamType.practice,
        )
        .order_by(ExamRecord.exam_date.desc(), ExamRecord.id.desc())
        .all()
    )
    return [r for r in rows if (r.question_stats or {}).get("mode") not in _NON_PRACTICE_MODES]


def _week_start(day: datetime.date) -> datetime.date:
    """ISO 周一为一周起点（本周一 00:00）。"""
    return day - datetime.timedelta(days=day.weekday())


def _iso_week_label(day: datetime.date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _exam_answers(db: Session, student_id: int, exam: ExamRecord) -> list[StudentAnswer]:
    return (
        db.query(StudentAnswer)
        .filter(StudentAnswer.student_id == student_id, StudentAnswer.exam_id == exam.id)
        .all()
    )


def _accuracy(correct: int, total: int) -> float:
    return round(correct / total, 4) if total else 0.0


def _streak_days(answers: list[StudentAnswer], today: datetime.date) -> int:
    """连续有作答的天数：去重日期，从今天向前数连续。"""
    answer_dates = {a.answered_at.date() for a in answers if a.answered_at}
    streak = 0
    day = today
    while day in answer_dates:
        streak += 1
        day -= datetime.timedelta(days=1)
    return streak


def _build_knowledge_points(
    answers: list[StudentAnswer], kp_map: dict[int, list[str]]
) -> list[dict]:
    """按知识点聚合正确率：mastery = 答对/总作答（一题多知识点分别计）。"""
    total: dict[str, int] = defaultdict(int)
    correct: dict[str, int] = defaultdict(int)
    for a in answers:
        for kp in kp_map.get(a.question_id, ()):
            total[kp] += 1
            if a.is_correct:
                correct[kp] += 1
    return [
        {"name": kp, "mastery": round(correct[kp] / t, 4)}
        for kp, t in sorted(total.items())
    ]


def build_student_report(
    db: Session, student_id: int, today: datetime.date | None = None
) -> dict:
    """聚合「我的」页全部数据：profile / stats / weekly / learning_plan。

    today 供测试注入固定日期；默认取当前本地日期。
    """
    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError(detail="学生不存在", error_code="STUDENT_NOT_FOUND")
    today = today or datetime.date.today()

    all_answers: list[StudentAnswer] = []
    weekly_exams: list[ExamRecord] = []
    weekly_answers: list[StudentAnswer] = []
    completed_ids: set[int] = set()
    monday = _week_start(today)

    for exam in _practice_records(db, student_id):
        answers = _exam_answers(db, student_id, exam)
        all_answers.extend(answers)
        if answers:
            completed_ids.add(exam.id)
        if monday <= exam.exam_date <= today:
            weekly_exams.append(exam)
            weekly_answers.extend(answers)

    weekly_qids = {a.question_id for a in weekly_answers}
    kp_map = {
        q.id: split_knowledge_points(q.knowledge_points)
        for q in db.query(Question).filter(Question.id.in_(weekly_qids)).all()
    }

    weekly_total = len(weekly_answers)
    weekly_correct = sum(1 for a in weekly_answers if a.is_correct)
    all_total = len(all_answers)
    all_correct = sum(1 for a in all_answers if a.is_correct)

    return {
        "profile": {
            "student_id": student.id,
            "name": student.name,
            "class_name": student.class_.name if student.class_ else "",
            "bind_code": student.bind_code,
        },
        "stats": {
            "completed_exercises": len(completed_ids),
            "accuracy": _accuracy(all_correct, all_total),
            "streak_days": _streak_days(all_answers, today),
        },
        "weekly": {
            "week_label": _iso_week_label(today),
            "exercises": sum(1 for e in weekly_exams if e.id in completed_ids),
            "accuracy": _accuracy(weekly_correct, weekly_total),
            "duration_hours": 0.0,  # 无时长数据源（设计 D5 占位）
            "knowledge_points": _build_knowledge_points(weekly_answers, kp_map),
            "teacher_comment": None,  # LLM 评语属 doc 57 weekly_report，本轮固定 null
        },
        "learning_plan": student.learning_plan or None,
    }
