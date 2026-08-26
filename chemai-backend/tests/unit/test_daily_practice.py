"""每日练习调度服务层单元测试（task 8.1-8.3）。

覆盖：画像映射生成 per-student 每日练习、同生同天去重、家长通知（绑定/无绑定）、
超期标记、分批 ≤5 与单学生失败不阻断。
"""
import datetime

import pytest

from app.db.models import (
    Class,
    ExamRecord,
    ExamType,
    Grade,
    Parent,
    ParentNotification,
    Question,
    ReviewTask,
    School,
    Student,
    StudentParentBinding,
)
from app.db.models.enums import (
    Difficulty,
    NotificationType,
    ParentBindingStatus,
    ReviewLevel,
    ReviewTaskStatus,
)
from app.services.exercise.daily import DailyPracticeScheduler


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


def _student(db_session, env, name="张三", profile=None):
    stu = Student(class_id=env["class_id"], name=name, barrier_profile=profile or {})
    db_session.add(stu)
    db_session.flush()
    return stu


def _load_bank(tmp_path):
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "真题.json").write_text('{"questions": ['
        '{"id": "q1", "content": "氧化还原A", "answer": "B", "knowledge_points": ["氧化还原反应"], "difficulty": "easy"},'
        '{"id": "q2", "content": "平衡A", "answer": "C", "knowledge_points": ["化学平衡"], "difficulty": "easy"},'
        '{"id": "q3", "content": "离子A", "answer": "D", "knowledge_points": ["离子反应"], "difficulty": "easy"}'
        "]}", encoding="utf-8")
    from app.services.question.historical import reload_bank
    return reload_bank(tmp_path)


def _review_task(db_session, env, stu, overdue=True):
    q = Question(content="c", answer="a", knowledge_points="氧化还原反应",
                 difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.flush()
    task = ReviewTask(
        student_id=stu.id, question_id=q.id, review_level=ReviewLevel.level1,
        status=ReviewTaskStatus.pending,
        next_review_at=datetime.datetime(2026, 1, 1) if overdue else datetime.datetime(2026, 9, 1),
    )
    db_session.add(task)
    db_session.flush()
    return task


# ---- 8.1 每日练习创建 ----

def test_create_daily_concept_default(tmp_path, db_session, env):
    _load_bank(tmp_path)
    stu = _student(db_session, env)  # 空画像 → concept
    result = DailyPracticeScheduler(db_session).create_daily_practice(stu)
    assert result["student_id"] == stu.id
    assert result["barrier"] == "concept"
    assert result["difficulty"] == "easy"  # 冷启动 medium + concept 降一档
    exam = db_session.get(ExamRecord, result["exam_id"])
    assert exam.student_id == stu.id
    assert exam.exam_type == ExamType.practice
    assert exam.question_stats["mode"] == "daily"
    deadline = exam.question_stats["deadline"]
    assert deadline == (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    assert result["question_count"] == 3


def test_create_daily_dedup_same_day(tmp_path, db_session, env):
    _load_bank(tmp_path)
    stu = _student(db_session, env)
    svc = DailyPracticeScheduler(db_session)
    now = datetime.datetime(2026, 8, 26, 8, 0, 0)
    assert svc.create_daily_practice(stu, now=now) is not None
    assert svc.create_daily_practice(stu, now=now) is None  # 同生同天去重


def test_create_daily_not_blocked_by_training_same_day(tmp_path, db_session, env):
    """同一天训练/变式记录不阻断每日练习（c1：去重只看 mode==daily）。"""
    _load_bank(tmp_path)
    stu = _student(db_session, env)
    db_session.add(ExamRecord(
        class_id=None, student_id=stu.id, name="错题训练",
        exam_type=ExamType.practice, exam_date=datetime.date(2026, 8, 26),
        question_stats={"mode": "training"},
    ))
    db_session.flush()
    now = datetime.datetime(2026, 8, 26, 8, 0, 0)
    result = DailyPracticeScheduler(db_session).create_daily_practice(stu, now=now)
    assert result is not None  # 训练记录不影响每日布置
    exam = db_session.get(ExamRecord, result["exam_id"])
    assert exam.question_stats["mode"] == "daily"


def test_create_daily_reading_keeps_zpd(tmp_path, db_session, env):
    _load_bank(tmp_path)
    stu = _student(db_session, env, profile={"reading": 1.0})
    result = DailyPracticeScheduler(db_session).create_daily_practice(stu)
    assert result["barrier"] == "reading"
    assert result["difficulty"] == "medium"  # reading 保持 ZPD 冷启动 medium


# ---- 8.2 家长通知 ----

def test_notify_parent_bound(tmp_path, db_session, env):
    _load_bank(tmp_path)
    stu = _student(db_session, env)
    parent = Parent(name="父", phone="13800000000")
    db_session.add(parent)
    db_session.flush()
    db_session.add(StudentParentBinding(parent_id=parent.id, student_id=stu.id, bind_code="123456",
                                        status=ParentBindingStatus.active))
    db_session.flush()
    svc = DailyPracticeScheduler(db_session)
    exam = db_session.get(ExamRecord, svc.create_daily_practice(stu)["exam_id"])
    assert svc.notify_parent(stu, exam) == 1
    note = db_session.query(ParentNotification).first()
    assert note.parent_id == parent.id
    assert note.notification_type == NotificationType.message
    assert "张三" in note.content


def test_notify_parent_unbound_silent(tmp_path, db_session, env):
    _load_bank(tmp_path)
    stu = _student(db_session, env)
    svc = DailyPracticeScheduler(db_session)
    exam = db_session.get(ExamRecord, svc.create_daily_practice(stu)["exam_id"])
    assert svc.notify_parent(stu, exam) == 0  # 无绑定静默跳过
    assert db_session.query(ParentNotification).count() == 0


# ---- 8.3 超期标记 ----

def test_mark_overdue_in_batch(tmp_path, db_session, env):
    _load_bank(tmp_path)
    stu = _student(db_session, env)
    overdue_task = _review_task(db_session, env, stu, overdue=True)
    future_task = _review_task(db_session, env, stu, overdue=False)
    summary = DailyPracticeScheduler(db_session).run_daily_batch(
        now=datetime.datetime(2026, 8, 26, 8, 0, 0)
    )
    assert summary["overdue"] == 1
    db_session.refresh(overdue_task)
    assert overdue_task.status == ReviewTaskStatus.overdue
    db_session.refresh(future_task)
    assert future_task.status == ReviewTaskStatus.pending


def test_batch_creates_for_multiple_and_skips(tmp_path, db_session, env):
    _load_bank(tmp_path)
    students = [_student(db_session, env, name=f"学生{i}") for i in range(6)]  # >5 分批
    now = datetime.datetime(2026, 8, 26, 8, 0, 0)
    summary = DailyPracticeScheduler(db_session).run_daily_batch(now=now)
    assert summary["created"] == 6
    assert summary["skipped"] == 0
    assert summary["failed"] == 0
    # 第二次运行全部跳过（去重）
    summary2 = DailyPracticeScheduler(db_session).run_daily_batch(now=now)
    assert summary2["created"] == 0
    assert summary2["skipped"] == 6


def test_single_student_failure_not_blocking(tmp_path, db_session, env, monkeypatch):
    _load_bank(tmp_path)
    students = [_student(db_session, env, name=f"学生{i}") for i in range(3)]
    svc = DailyPracticeScheduler(db_session)
    original = svc.create_daily_practice
    calls = {"n": 0}

    def flaky(student, now=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("模拟失败")
        return original(student, now=now)

    monkeypatch.setattr(svc, "create_daily_practice", flaky)
    summary = svc.run_daily_batch(now=datetime.datetime(2026, 8, 26, 8, 0, 0))
    assert summary["failed"] == 1
    assert summary["created"] == 2  # 失败不阻断后续
