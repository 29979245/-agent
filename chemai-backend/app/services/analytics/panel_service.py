"""学情面板服务：批量 SQL 拉取 + 内存聚合（design.md D1/D3/D6/D7）。

数据源：Class/Student/ExamRecord/StudentAnswer/Question。均分仅聚合
exam_type=exam 且已发布/完成的正式考试（D3），排除 per-student ZPD 练习。
"""
import html
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.models import Class, ExamRecord, Grade, Question, Student, StudentAnswer, Teacher
from app.db.models.enums import ExamStatus, ExamType
from app.services.analytics.aggregation import (
    TREND_POINTS,
    build_class_panel,
    dominant_barrier_counts,
    weighted_exam_average,
)
from app.services.diagnosis.aggregation import normalize_profile
from app.services.question.serializers import split_knowledge_points

PUBLISHED_EXAM_STATUSES = (ExamStatus.published, ExamStatus.completed)
STUDENT_COMPACT_TREND_POINTS = 6


def _require_class(db: Session, class_id: int) -> Class:
    cls = db.get(Class, class_id)
    if cls is None:
        raise NotFoundError()
    return cls


def _class_exam_records(db: Session, class_id: int) -> list[ExamRecord]:
    """班级正式考试（exam 类型、已发布/完成），按考试日期升序。"""
    return (
        db.query(ExamRecord)
        .filter(
            ExamRecord.class_id == class_id,
            ExamRecord.exam_type == ExamType.exam,
            ExamRecord.status.in_(PUBLISHED_EXAM_STATUSES),
        )
        .order_by(ExamRecord.exam_date.asc())
        .all()
    )


def _question_kp_map(db: Session, question_ids: Iterable[int]) -> dict[int, list[str]]:
    ids = list(question_ids)
    if not ids:
        return {}
    rows = (
        db.query(Question.id, Question.knowledge_points)
        .filter(Question.id.in_(ids))
        .all()
    )
    return {qid: split_knowledge_points(kps) for qid, kps in rows}


def _exam_accuracy_points(db: Session, exams: list[ExamRecord]) -> list[tuple]:
    """按考试计算全班平均正确率：每个考试一次作答集合 → (exam_date, accuracy)。"""
    exam_ids = [e.id for e in exams]
    if not exam_ids:
        return []
    answers = (
        db.query(StudentAnswer).filter(StudentAnswer.exam_id.in_(exam_ids)).all()
    )
    by_exam: dict[int, list[StudentAnswer]] = defaultdict(list)
    for a in answers:
        by_exam[a.exam_id].append(a)
    points = []
    for e in exams:
        ans = by_exam.get(e.id, [])
        if not ans:
            continue
        acc = sum(1 for a in ans if a.is_correct) / len(ans)
        points.append((e.exam_date, acc))
    return points


def _students_exam_accuracy_points(
    db: Session, class_id: int, student_ids: list[int]
) -> dict[int, list[tuple]]:
    """按考试计算每个学生的个人正确率：student_id → [(exam_date, accuracy)] 升序。

    仅聚合正式考试（exam 类型、已发布/完成），与班级均分口径一致（design.md D3）。
    """
    result: dict[int, list[tuple]] = {sid: [] for sid in student_ids}
    if not student_ids:
        return result
    exams = _class_exam_records(db, class_id)
    exam_ids = [e.id for e in exams]
    if not exam_ids:
        return result
    answers = (
        db.query(StudentAnswer)
        .filter(
            StudentAnswer.student_id.in_(student_ids),
            StudentAnswer.exam_id.in_(exam_ids),
        )
        .all()
    )
    by_student_exam: dict[tuple[int, int], list[bool]] = defaultdict(list)
    for a in answers:
        by_student_exam[(a.student_id, a.exam_id)].append(a.is_correct)
    for e in exams:
        for sid in student_ids:
            accs = by_student_exam.get((sid, e.id))
            if not accs:
                continue
            acc = sum(1 for c in accs if c) / len(accs)
            result[sid].append((e.exam_date, acc))
    return result


def _score_trend_items(points: list[tuple], limit: int) -> list[dict]:
    """成绩趋势点数组：按时间升序，取最近 limit 个，均分转百分比。"""
    return [
        {
            "exam_date": ts.isoformat() if isinstance(ts, date) else ts.date().isoformat(),
            "score": round(acc * 100, 2),
        }
        for ts, acc in points[-limit:]
    ]


# ---------------- 面板端点数据 ----------------

def load_class_panel(db: Session, class_id: int) -> dict:
    """GET /api/panel/class/{class_id}：班级学情面板完整数据。"""
    cls = _require_class(db, class_id)
    students = db.query(Student).filter(Student.class_id == class_id).all()
    exams = _class_exam_records(db, class_id)
    exam_points = _exam_accuracy_points(db, exams)
    answer_rows = (
        db.query(StudentAnswer)
        .filter(StudentAnswer.exam_id.in_([e.id for e in exams]))
        .all()
    ) if exams else []
    kp_map = _question_kp_map(db, {a.question_id for a in answer_rows})
    return build_class_panel(
        class_id=class_id,
        class_name=cls.name,
        students=[s.barrier_profile for s in students],
        exam_points=exam_points,
        rows=[(a.question_id, a.is_correct) for a in answer_rows],
        kp_map=kp_map,
    )


def load_knowledge_detail(db: Session, class_id: int, knowledge_point: str) -> dict:
    """GET /api/panel/class/{class_id}/knowledge/{kp}：指定知识点错误率与出错学生。"""
    _require_class(db, class_id)
    exams = _class_exam_records(db, class_id)
    if not exams:
        return _empty_knowledge_detail(class_id, knowledge_point)
    rows = (
        db.query(StudentAnswer, Question)
        .join(Question, StudentAnswer.question_id == Question.id)
        .filter(StudentAnswer.exam_id.in_([e.id for e in exams]))
        .all()
    )
    names = {
        s.id: s.name
        for s in db.query(Student).filter(Student.class_id == class_id).all()
    }
    total = 0
    error_count = 0
    wrong: dict[int, int] = defaultdict(int)
    for ans, q in rows:
        if knowledge_point not in split_knowledge_points(q.knowledge_points):
            continue
        total += 1
        if not ans.is_correct:
            error_count += 1
            wrong[ans.student_id] += 1
    students = [
        {"student_id": sid, "name": names.get(sid, ""), "wrong_count": cnt}
        for sid, cnt in sorted(wrong.items(), key=lambda x: x[1], reverse=True)
    ]
    return {
        "class_id": class_id,
        "knowledge_point": knowledge_point,
        "total": total,
        "error_count": error_count,
        "error_rate": round(error_count / total * 100, 2) if total else 0.0,
        "students": students,
    }


def _empty_knowledge_detail(class_id: int, knowledge_point: str) -> dict:
    return {
        "class_id": class_id,
        "knowledge_point": knowledge_point,
        "total": 0,
        "error_count": 0,
        "error_rate": 0.0,
        "students": [],
    }


def load_student_detail(db: Session, class_id: int, student_id: int) -> dict:
    """GET /api/panel/class/{class_id}/student/{student_id}：学生学情详情。

    学生不属于该班级 → 404（spec「学生不属于该班级」）。
    """
    _require_class(db, class_id)
    student = db.get(Student, student_id)
    if student is None or student.class_id != class_id:
        raise NotFoundError()
    score_points = _students_exam_accuracy_points(
        db, class_id, [student_id]
    ).get(student_id, [])
    # KPI：练习次数（完成过的练习 = 有作答的 practice 考试）、平均正确率、最后活跃
    practice_ids = _student_practice_ids(db, student_id)
    exercises_completed = 0
    if practice_ids:
        exercises_completed = (
            db.query(StudentAnswer.exam_id)
            .filter(StudentAnswer.exam_id.in_(practice_ids))
            .distinct()
            .count()
        )
    all_answers = (
        db.query(StudentAnswer).filter(StudentAnswer.student_id == student_id).all()
    )
    total = len(all_answers)
    accuracy = round(sum(1 for a in all_answers if a.is_correct) / total, 4) if total else 0.0
    last_exercise_at = (
        _student_activity_map(db, [student]).get(student_id, {}).get("last_exercise_at")
    )
    activity_rows = (
        db.query(StudentAnswer, Question, ExamRecord)
        .join(Question, StudentAnswer.question_id == Question.id)
        .outerjoin(ExamRecord, StudentAnswer.exam_id == ExamRecord.id)
        .filter(StudentAnswer.student_id == student_id)
        .order_by(StudentAnswer.answered_at.desc(), StudentAnswer.id.desc())
        .limit(20)
        .all()
    )
    recent_activity = [
        {
            "date": ans.answered_at.date().isoformat() if ans.answered_at else None,
            "answered_at": ans.answered_at.isoformat() if ans.answered_at else None,
            "is_correct": ans.is_correct,
            "exam_name": exam.name if exam else "练习",
            "content": q.content,
            "barrier_type": ans.barrier_type.value if ans.barrier_type else None,
        }
        for ans, q, exam in activity_rows
    ]
    wrong_rows = (
        db.query(StudentAnswer, Question)
        .join(Question, StudentAnswer.question_id == Question.id)
        .filter(StudentAnswer.student_id == student_id, StudentAnswer.is_correct.is_(False))
        .order_by(StudentAnswer.id.desc())
        .limit(50)
        .all()
    )
    weak: Counter = Counter()
    wrong_history = []
    for ans, q in wrong_rows:
        for kp in split_knowledge_points(q.knowledge_points):
            weak[kp] += 1
        wrong_history.append(
            {
                "question_id": ans.question_id,
                "content": q.content,
                "answer_text": ans.answer_text,
                "is_correct": ans.is_correct,
                "barrier_type": ans.barrier_type.value if ans.barrier_type else None,
                "answered_at": ans.answered_at.isoformat() if ans.answered_at else None,
            }
        )
    return {
        "class_id": class_id,
        "student_id": student_id,
        "name": student.name,
        "barrier_profile": normalize_profile(student.barrier_profile),
        "weak_knowledge_points": [kp for kp, _ in weak.most_common(5)],
        "score_trend": _score_trend_items(score_points, limit=TREND_POINTS),
        "wrong_history": wrong_history,
        "exercises_completed": exercises_completed,
        "accuracy": accuracy,
        "last_exercise_at": last_exercise_at,
        "recent_activity": recent_activity,
    }


def load_trend(db: Session, class_id: int) -> dict:
    """GET /api/panel/class/{class_id}/trend：班级均分趋势（与 avg_score_trend 同源）。"""
    _require_class(db, class_id)
    exams = _class_exam_records(db, class_id)
    points = _exam_accuracy_points(db, exams)
    trend = [
        {
            "exam_date": ts.isoformat() if isinstance(ts, date) else ts.date().isoformat(),
            "avg_score": round(acc * 100, 2),
        }
        for ts, acc in points
    ]
    return {"class_id": class_id, "trend": trend[-TREND_POINTS:]}


def load_grade_trend(db: Session, grade_id: int) -> dict:
    """GET /api/panel/grade/{grade_id}/trend：年级全部班级正式考试按 exam_date 归组均分。

    跨该年级全部班级聚合作答（design.md D6），按考试日期升序，最多最近 TREND_POINTS 点。
    """
    grade = db.get(Grade, grade_id)
    if grade is None:
        raise NotFoundError()
    class_ids = [c.id for c in db.query(Class).filter(Class.grade_id == grade_id).all()]
    exams = (
        db.query(ExamRecord)
        .filter(
            ExamRecord.class_id.in_(class_ids),
            ExamRecord.exam_type == ExamType.exam,
            ExamRecord.status.in_(PUBLISHED_EXAM_STATUSES),
        )
        .all()
    ) if class_ids else []
    exam_ids = [e.id for e in exams]
    if not exam_ids:
        return {"grade_id": grade_id, "trend": []}
    exam_date_by_id = {e.id: e.exam_date for e in exams}
    answers = (
        db.query(StudentAnswer).filter(StudentAnswer.exam_id.in_(exam_ids)).all()
    )
    by_date: dict[date, list[bool]] = defaultdict(list)
    for a in answers:
        by_date[exam_date_by_id[a.exam_id]].append(a.is_correct)
    points = [
        (d, sum(1 for c in cs if c) / len(cs))
        for d, cs in sorted(by_date.items())
    ]
    trend = [
        {
            "exam_date": d.isoformat() if isinstance(d, date) else str(d),
            "avg_score": round(acc * 100, 2),
        }
        for d, acc in points[-TREND_POINTS:]
    ]
    return {"grade_id": grade_id, "trend": trend}


def load_dashboard(db: Session, teacher_id: int) -> dict:
    """GET /api/panel/dashboard/{teacher_id}：教师首页概览（本校班级）。"""
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise NotFoundError()
    classes = (
        db.query(Class)
        .join(Grade, Class.grade_id == Grade.id)
        .filter(Grade.school_id == teacher.school_id)
        .order_by(Class.id)
        .all()
    )
    return {
        "teacher_id": teacher_id,
        "classes": [_class_metrics(db, c) for c in classes],
    }


def _class_metrics(db: Session, cls: Class) -> dict:
    """单个班级概览：均分（衰减加权）+ 障碍分布 + 练习统计。"""
    students = db.query(Student).filter(Student.class_id == cls.id).all()
    exams = _class_exam_records(db, cls.id)
    points = _exam_accuracy_points(db, exams)
    weighted = weighted_exam_average(points)
    student_ids = [s.id for s in students]
    practice_count = 0
    if student_ids:
        practice_count = (
            db.query(func.count(ExamRecord.id))
            .filter(
                ExamRecord.student_id.in_(student_ids),
                ExamRecord.exam_type == ExamType.practice,
            )
            .scalar()
            or 0
        )
    return {
        "class_id": cls.id,
        "class_name": cls.name,
        "total_students": len(students),
        "recent_exam_avg": round(weighted * 100, 2) if weighted is not None else None,
        "barrier_distribution": dominant_barrier_counts(
            s.barrier_profile for s in students
        ),
        "practice_count": practice_count,
    }


def _student_practice_ids(db: Session, student_id: int) -> list[int]:
    """该生的练习考试 id（exam_type=practice，排除 training/variant 会话）。"""
    rows = (
        db.query(ExamRecord)
        .filter(
            ExamRecord.student_id == student_id,
            ExamRecord.exam_type == ExamType.practice,
        )
        .all()
    )
    return [
        r.id
        for r in rows
        if (r.question_stats or {}).get("mode") not in ("training", "variant")
    ]


def _student_activity_map(db: Session, students: list[Student]) -> dict[int, dict]:
    """学生活跃状态：student_id → {"is_active", "last_exercise_at"}。

    最近一次作答（任意作答）距今 ≤ 7 天为活跃；从未作答按注册时间 created_at 判定。
    一次 GROUP BY student_id 的 MAX(answered_at) 查询聚合（design.md D6）。
    """
    if not students:
        return {}
    student_ids = [s.id for s in students]
    rows = (
        db.query(StudentAnswer.student_id, func.max(StudentAnswer.answered_at))
        .filter(StudentAnswer.student_id.in_(student_ids))
        .group_by(StudentAnswer.student_id)
        .all()
    )
    last_by_student = {sid: ts for sid, ts in rows}
    created_by_student = {s.id: s.created_at for s in students}
    now = datetime.utcnow()
    window = 7 * 24 * 3600
    result = {}
    for sid in student_ids:
        last = last_by_student.get(sid)
        if last is not None:
            is_active = (now - last).total_seconds() <= window
            last_iso = last.isoformat()
        else:
            created = created_by_student.get(sid)
            is_active = created is not None and (now - created).total_seconds() <= window
            last_iso = None
        result[sid] = {"is_active": is_active, "last_exercise_at": last_iso}
    return result


def class_students(db: Session, class_id: int) -> dict:
    """GET /api/classes/{class_id}/students：班级学生列表（id/name/barrier/趋势/活跃）。"""
    cls = _require_class(db, class_id)
    students = (
        db.query(Student).filter(Student.class_id == class_id).order_by(Student.id).all()
    )
    student_ids = [s.id for s in students]
    points_by_student = _students_exam_accuracy_points(db, class_id, student_ids)
    activity_by_student = _student_activity_map(db, students)
    return {
        "class_id": class_id,
        "class_name": cls.name,
        "items": [
            {
                "id": s.id,
                "name": s.name,
                "barrier_profile": normalize_profile(s.barrier_profile),
                "score_trend": _score_trend_items(
                    points_by_student.get(s.id, []),
                    limit=STUDENT_COMPACT_TREND_POINTS,
                ),
                **activity_by_student.get(s.id, {"is_active": False, "last_exercise_at": None}),
            }
            for s in students
        ],
    }


# ---------------- PDF 导出 ----------------

def panel_report_html(panel: dict) -> str:
    """班级学情报告 HTML（复用 paper-export 的 xhtml2pdf + SimSun 栈），仅含本班数据。"""
    ov = panel["class_overview"]
    kp_rows = ""
    for kp in panel["knowledge_points"]:
        kp_rows += (
            f"<tr><td>{html.escape(kp['knowledge_point'])}</td>"
            f"<td>{kp['error_rate']}%</td><td>{kp['errors']}/{kp['total']}</td></tr>"
        )
    barrier = panel["barrier_distribution"]
    barrier_cells = "".join(
        f'<div class="card"><b>{barrier[axis]}</b>{axis}</div>'
        for axis in ("concept", "reading", "expression")
    )
    recent = ov["recent_exam_avg"]
    recent_str = f"{recent}" if recent is not None else "暂无"
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{html.escape(ov['class_name'])} 班级学情报告</title>
<style>
@page {{ size: A4; margin: 2cm; }}
body {{ font-family: SimSun, stsong-light, sans-serif; font-size: 11pt; color: #000; }}
h1 {{ text-align: center; font-size: 16pt; }}
.cards {{ display: table; width: 100%; margin: 12px 0; }}
.card {{ display: table-cell; text-align: center; border: 1px solid #000; padding: 8px; }}
.card b {{ display: block; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 12px; }}
th, td {{ border: 1px solid #000; padding: 4px 6px; }}
th {{ background: #eee; }}
</style></head><body>
<h1>{html.escape(ov['class_name'])} 班级学情报告</h1>
<div class="cards">
  <div class="card"><b>{ov['total_students']}</b>学生数</div>
  <div class="card"><b>{ov['exam_count']}</b>考试次数</div>
  <div class="card"><b>{recent_str}</b>最近均分</div>
</div>
<div class="cards">{barrier_cells}</div>
<h2>知识点错误率 TOP{len(panel['knowledge_points'])}</h2>
<table><tr><th>知识点</th><th>错误率</th><th>错/总</th></tr>{kp_rows}</table>
</body></html>"""
