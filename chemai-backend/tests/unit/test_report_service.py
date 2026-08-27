"""学生个人报告聚合服务测试（change student-supplement-apis task 2.1/2.2）。

覆盖：空数据兜底、有数据统计口径、training/variant 排除、连续打卡断档、跨周边界、知识点聚合。
"""
import datetime

import pytest

from app.core.exceptions import NotFoundError
from app.db.models import (
    Class,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Question,
    School,
    Student,
    StudentAnswer,
)
from app.db.models.enums import Difficulty
from app.services.analytics.report_service import build_student_report

TODAY = datetime.date(2026, 8, 27)  # 周四


def _at(day: datetime.date, hour: int = 10) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time(hour, 0))


@pytest.fixture()
def env(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    return {"class_id": cls.id}


def _student(db_session, env, name="张三", **kw):
    stu = Student(class_id=env["class_id"], name=name, **kw)
    db_session.add(stu)
    db_session.flush()
    return stu


def _question(db_session, kps="氧化还原反应", answer="B"):
    q = Question(content="题目", answer=answer, knowledge_points=kps, difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.flush()
    return q


def _practice(db_session, stu, day, mode=None, name="练习", status=ExamStatus.published):
    exam = ExamRecord(
        student_id=stu.id,
        name=name,
        status=status,
        exam_type=ExamType.practice,
        exam_date=day,
        question_stats={"mode": mode} if mode else {},
    )
    db_session.add(exam)
    db_session.flush()
    return exam


def _answer(db_session, stu, exam, q, day, correct=True):
    db_session.add(
        StudentAnswer(
            student_id=stu.id,
            question_id=q.id,
            exam_id=exam.id,
            answer_text=q.answer,
            is_correct=correct,
            answered_at=_at(day),
        )
    )
    db_session.flush()


# ---------------- 空数据兜底 ----------------

def test_empty_data(db_session, env):
    stu = _student(db_session, env)
    report = build_student_report(db_session, stu.id, today=TODAY)
    assert report["profile"] == {
        "student_id": stu.id,
        "name": "张三",
        "class_name": "1班",
        "bind_code": "",
    }
    assert report["stats"] == {
        "completed_exercises": 0,
        "accuracy": 0.0,
        "streak_days": 0,
    }
    assert report["weekly"]["exercises"] == 0
    assert report["weekly"]["accuracy"] == 0.0
    assert report["weekly"]["duration_hours"] == 0.0
    assert report["weekly"]["knowledge_points"] == []
    assert report["weekly"]["teacher_comment"] is None
    assert report["learning_plan"] is None


def test_missing_student_404(db_session, env):
    _student(db_session, env)
    with pytest.raises(NotFoundError):
        build_student_report(db_session, 999, today=TODAY)


# ---------------- 有数据统计口径 ----------------

def test_stats_accuracy_and_completed(db_session, env):
    stu = _student(db_session, env)
    q1 = _question(db_session, kps="氧化还原反应", answer="B")
    q2 = _question(db_session, kps="离子反应", answer="C")
    exam = _practice(db_session, stu, TODAY)
    _answer(db_session, stu, exam, q1, TODAY, correct=True)
    _answer(db_session, stu, exam, q2, TODAY, correct=False)
    report = build_student_report(db_session, stu.id, today=TODAY)
    assert report["stats"]["completed_exercises"] == 1
    assert report["stats"]["accuracy"] == 0.5


def test_training_variant_excluded(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    practice = _practice(db_session, stu, TODAY)
    training = _practice(db_session, stu, TODAY, mode="training")
    variant = _practice(db_session, stu, TODAY, mode="variant")
    _answer(db_session, stu, practice, q, TODAY, correct=True)
    _answer(db_session, stu, training, q, TODAY, correct=False)
    _answer(db_session, stu, variant, q, TODAY, correct=False)
    report = build_student_report(db_session, stu.id, today=TODAY)
    # 仅 practice 计入：1 题 1 对 → accuracy 1.0，练习数 1
    assert report["stats"]["completed_exercises"] == 1
    assert report["stats"]["accuracy"] == 1.0
    assert report["weekly"]["exercises"] == 1


# ---------------- 连续打卡断档 ----------------

def test_streak_days_counts_backward_until_gap(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    exam_today = _practice(db_session, stu, TODAY)
    exam_yesterday = _practice(db_session, stu, TODAY - datetime.timedelta(days=1))
    exam_2d = _practice(db_session, stu, TODAY - datetime.timedelta(days=2))
    exam_4d = _practice(db_session, stu, TODAY - datetime.timedelta(days=4))  # 断档：3 天前无作答
    _answer(db_session, stu, exam_today, q, TODAY)
    _answer(db_session, stu, exam_yesterday, q, TODAY - datetime.timedelta(days=1))
    _answer(db_session, stu, exam_2d, q, TODAY - datetime.timedelta(days=2))
    _answer(db_session, stu, exam_4d, q, TODAY - datetime.timedelta(days=4))
    report = build_student_report(db_session, stu.id, today=TODAY)
    assert report["stats"]["streak_days"] == 3  # 今/昨/前天连续，3 天前断档


def test_streak_days_zero_without_today(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    exam = _practice(db_session, stu, TODAY - datetime.timedelta(days=1))
    _answer(db_session, stu, exam, q, TODAY - datetime.timedelta(days=1))
    report = build_student_report(db_session, stu.id, today=TODAY)
    assert report["stats"]["streak_days"] == 0  # 今天无作答即断档


# ---------------- 跨周边界 ----------------

def test_weekly_only_current_week(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    monday = TODAY - datetime.timedelta(days=TODAY.weekday())  # 本周一
    last_week = monday - datetime.timedelta(days=1)  # 上周日
    in_week = _practice(db_session, stu, monday)
    out_week = _practice(db_session, stu, last_week)
    _answer(db_session, stu, in_week, q, monday, correct=True)
    _answer(db_session, stu, out_week, q, last_week, correct=False)
    report = build_student_report(db_session, stu.id, today=TODAY)
    # weekly 只含本周：1 题 1 对；stats 口径含全部
    assert report["weekly"]["week_label"] == "2026-W35"
    assert report["weekly"]["exercises"] == 1
    assert report["weekly"]["accuracy"] == 1.0
    assert report["stats"]["completed_exercises"] == 2
    assert report["stats"]["accuracy"] == 0.5


# ---------------- 知识点聚合 ----------------

def test_knowledge_points_aggregation(db_session, env):
    stu = _student(db_session, env)
    q1 = _question(db_session, kps="氧化还原反应,化学平衡", answer="B")
    q2 = _question(db_session, kps="离子反应", answer="C")
    exam = _practice(db_session, stu, TODAY)
    _answer(db_session, stu, exam, q1, TODAY, correct=True)   # 氧化还原✓ 化学平衡✓
    _answer(db_session, stu, exam, q2, TODAY, correct=False)  # 离子反应✗
    report = build_student_report(db_session, stu.id, today=TODAY)
    by_name = {kp["name"]: kp["mastery"] for kp in report["weekly"]["knowledge_points"]}
    assert by_name == {"氧化还原反应": 1.0, "化学平衡": 1.0, "离子反应": 0.0}


# ---------------- 学习计划与绑定码 ----------------

def test_learning_plan_and_bind_code(db_session, env):
    stu = _student(db_session, env, bind_code="AB12CD", learning_plan={"items": ["x"]})
    report = build_student_report(db_session, stu.id, today=TODAY)
    assert report["profile"]["bind_code"] == "AB12CD"
    assert report["learning_plan"] == {"items": ["x"]}


def test_empty_learning_plan_null(db_session, env):
    stu = _student(db_session, env)
    report = build_student_report(db_session, stu.id, today=TODAY)
    assert report["learning_plan"] is None
