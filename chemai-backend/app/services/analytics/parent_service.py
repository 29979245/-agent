"""家长视角报告聚合（design.md D3）。

不复用 build_student_report 的返回结构——它含家长不可见字段（bind_code、teacher_comment）。
本服务复用 report_service 的公开周数据收集器（practice_answers/week_answers/week_stats/
week_start/accuracy/streak_days/build_knowledge_points）组装家长形态：
4 统计卡 + 知识掌握概览 + 通俗学习特点 + 本周动态时间线。响应显式不含绑定码/排名/教师评语。
"""
import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.models import Question
from app.services.analytics.report_service import (
    accuracy,
    build_knowledge_points,
    practice_answers,
    streak_days,
    week_answers,
    week_start,
    week_stats,
)
from app.services.question.serializers import split_knowledge_points


def _learning_style(weekly_accuracy: float, streak: int) -> str:
    """由数据推导的通俗学习特点（非 LLM，静态模板）。"""
    if streak >= 3 and weekly_accuracy >= 0.8:
        return "最近一直在坚持练习，正确率也很高，状态很好"
    if streak >= 3:
        return "连续学习习惯已养成，正确率还有提升空间"
    if weekly_accuracy >= 0.8:
        return "本周练习正确率高，但学习连续性可以加强"
    if streak == 0:
        return "本周还没有完成练习，可以安排些时间开始"
    return "本周有练习记录，继续保持节奏，正确率会逐步提升"


def _build_timeline(weekly_answers: list, today: datetime.date) -> list[dict]:
    """本周动态时间线：按作答日期聚合正确/总量/正确率。"""
    by_day: dict[datetime.date, list] = {}
    for a in weekly_answers:
        if not a.answered_at:
            continue
        day = a.answered_at.date()
        cell = by_day.setdefault(day, [0, 0])
        cell[0] += 1 if a.is_correct else 0
        cell[1] += 1
    return [
        {
            "date": day.isoformat(),
            "correct": cell[0],
            "total": cell[1],
            "accuracy": accuracy(cell[0], cell[1]),
        }
        for day, cell in sorted(by_day.items())
    ]


def build_parent_report(
    db: Session, student_id: int, today: datetime.date | None = None
) -> dict:
    """家长视角报告：统计卡 + 知识掌握概览 + 通俗学习特点 + 本周时间线。"""
    from app.db.models import Student

    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError(detail="学生不存在", error_code="STUDENT_NOT_FOUND")
    today = today or datetime.date.today()
    monday = week_start(today)

    all_answers, completed_ids = practice_answers(db, student_id)
    weekly_answers = week_answers(db, student_id, monday, today)
    weekly = week_stats(db, student_id, monday, today)

    weekly_qids = {a.question_id for a in weekly_answers}
    kp_map = {
        q.id: split_knowledge_points(q.knowledge_points)
        for q in db.query(Question).filter(Question.id.in_(weekly_qids)).all()
    }

    all_total = len(all_answers)
    all_correct = sum(1 for a in all_answers if a.is_correct)
    streak = streak_days(all_answers, today)

    return {
        "student": {
            "student_id": student.id,
            "name": student.name,
            "class_name": student.class_.name if student.class_ else "",
        },
        "stats": {
            "weekly_exercises": weekly["practice_count"],
            "weekly_accuracy": weekly["accuracy"],
            "streak_days": streak,
            "total_practices": len(completed_ids),
            "total_answers": all_total,
            "overall_accuracy": accuracy(all_correct, all_total),
        },
        "knowledge_points": build_knowledge_points(weekly_answers, kp_map),
        "learning_style": _learning_style(weekly["accuracy"], streak),
        "timeline": _build_timeline(weekly_answers, today),
    }
