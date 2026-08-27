"""change parent-backend 3.5：业务埋点接入。

断言：预警创建触发 warning.triggered、每日练习布置触发 practice.assigned；
埋点异常不阻断既有流程（预警仍创建 / 练习仍布置）。
"""
import datetime

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
    WarningLog,
)
from app.db.models.enums import Difficulty, WebhookEventType
from app.services.analytics import early_warning
from app.services.analytics.early_warning import EarlyWarningService
from app.services.exercise import daily as daily_module
from app.services.exercise.daily import DailyPracticeScheduler

NOW = datetime.datetime(2026, 8, 26, 12, 0, 0)


def _stale_student(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    stu = Student(class_id=cls.id, name="张三", created_at=NOW - datetime.timedelta(days=30))
    db_session.add(stu)
    db_session.flush()
    q = Question(content="c", answer="a", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.flush()
    exam = ExamRecord(
        student_id=stu.id, name="T", exam_type=ExamType.practice,
        status=ExamStatus.published, exam_date=(NOW - datetime.timedelta(days=5)).date(),
    )
    db_session.add(exam)
    db_session.flush()
    db_session.add(
        StudentAnswer(
            student_id=stu.id, question_id=q.id, exam_id=exam.id, is_correct=True,
            answered_at=NOW - datetime.timedelta(days=5),
        )
    )
    db_session.flush()
    return stu


def test_warning_triggered_emit_on_check_student(db_session, monkeypatch):
    stu = _stale_student(db_session)
    emitted = []

    def fake_emit(db, event_type, payload):
        emitted.append((event_type, payload))

    monkeypatch.setattr(early_warning, "emit_event", fake_emit)
    result = EarlyWarningService(db_session).check_student(stu, now=NOW)
    assert result["created"] == 1  # 既有流程未受影响
    assert db_session.query(WarningLog).count() == 1
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == WebhookEventType.warning_triggered
    assert payload["student_id"] == stu.id
    assert payload["warning_type"] == "no_login"
    assert payload["title"]


def test_practice_assigned_emit_on_create_daily(tmp_path, db_session, monkeypatch):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    stu = Student(class_id=cls.id, name="张三", barrier_profile={})
    db_session.add(stu)
    db_session.flush()
    _load_bank(tmp_path)
    emitted = []

    def fake_emit(db, event_type, payload):
        emitted.append((event_type, payload))

    monkeypatch.setattr(daily_module, "emit_event", fake_emit)
    result = DailyPracticeScheduler(db_session).create_daily_practice(stu)
    assert result is not None  # 既有流程未受影响
    assert db_session.query(ExamRecord).filter(ExamRecord.student_id == stu.id).count() == 1
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == WebhookEventType.practice_assigned
    assert payload["student_id"] == stu.id
    assert payload["practice_name"] == "每日练习"
    assert payload["exam_id"] == result["exam_id"]


def test_emit_exception_does_not_break_flow(db_session, monkeypatch):
    from app.services.integration import webhook_service

    stu = _stale_student(db_session)

    def boom(db, event_type, payload, timestamp=None, backoff_base=0.0):
        raise RuntimeError("webhook offline")

    # 真实 emit_event 捕获 trigger_event 异常；此处让 trigger_event 抛错验证兜底
    monkeypatch.setattr(webhook_service, "trigger_event", boom)
    result = EarlyWarningService(db_session).check_student(stu, now=NOW)
    assert result["created"] == 1  # 埋点失败也不阻断预警创建
    assert db_session.query(WarningLog).count() == 1


def _load_bank(tmp_path):
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "真题.json").write_text(
        '{"questions": ['
        '{"id": "q1", "content": "氧化还原A", "answer": "B", "options": ["A","B","C","D"], "knowledge_points": ["氧化还原反应"], "difficulty": "easy"},'
        '{"id": "q2", "content": "平衡A", "answer": "C", "options": ["A","B","C","D"], "knowledge_points": ["化学平衡"], "difficulty": "easy"},'
        '{"id": "q3", "content": "离子A", "answer": "D", "options": ["A","B","C","D"], "knowledge_points": ["离子反应"], "difficulty": "easy"}'
        "]}",
        encoding="utf-8",
    )
    from app.services.question.historical import reload_bank

    reload_bank(tmp_path)
