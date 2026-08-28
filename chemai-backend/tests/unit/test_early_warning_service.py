"""预警引擎检测测试：纯函数（2.1-2.3）+ check_all_warnings 组装（2.4）。"""
import datetime

from app.db.models import (
    Class,
    ExamRecord,
    Grade,
    Question,
    School,
    Student,
    StudentAnswer,
    WarningLog,
)
from app.db.models.enums import Difficulty, ExamStatus, ExamType
from app.services.analytics.early_warning import (
    EarlyWarningService,
    detect_high_error_rate,
    detect_no_login,
    detect_score_drop,
)

NOW = datetime.datetime(2026, 8, 26, 12, 0, 0)


# ---------------- 2.1 detect_no_login ----------------

def test_no_login_after_threshold():
    assert detect_no_login(NOW - datetime.timedelta(days=4), NOW, NOW) == "warning"


def test_no_login_exact_threshold():
    assert detect_no_login(NOW - datetime.timedelta(days=3), NOW, NOW) == "warning"


def test_no_login_below_threshold():
    assert detect_no_login(NOW - datetime.timedelta(days=2), NOW, NOW) is None


def test_no_login_never_answered_uses_created_at():
    assert detect_no_login(None, NOW - datetime.timedelta(days=4), NOW) == "warning"


def test_no_login_never_answered_below_threshold():
    assert detect_no_login(None, NOW - datetime.timedelta(days=2), NOW) is None


def test_no_login_created_at_none_skips():
    assert detect_no_login(None, None, NOW) is None


def test_no_login_prefers_last_exercise():
    """曾作答以 last_exercise 为准，created_at 不参与（注册久但近期活跃不触发）。"""
    assert detect_no_login(NOW - datetime.timedelta(days=1), NOW - datetime.timedelta(days=30), NOW) is None


# ---------------- 2.2 detect_score_drop ----------------

def _pts(*accs):
    return [(datetime.date(2026, 8, 1 + i), acc) for i, acc in enumerate(accs)]


def test_score_drop_critical():
    assert detect_score_drop(_pts(0.9, 0.6)) == "critical"


def test_score_drop_warning():
    assert detect_score_drop(_pts(0.9, 0.8)) == "warning"


def test_score_drop_just_above_warning():
    """降幅 0.125（> 0.1 且 < 0.2）→ warning。"""
    assert detect_score_drop(_pts(1.0, 0.875)) == "warning"


def test_score_drop_just_above_critical():
    """降幅 0.25（> 0.2）→ critical。"""
    assert detect_score_drop(_pts(1.0, 0.75)) == "critical"


def test_score_drop_just_below_threshold():
    """降幅 0.0625（< 0.1）→ 不触发。"""
    assert detect_score_drop(_pts(1.0, 0.9375)) is None


def test_score_drop_no_drop():
    assert detect_score_drop(_pts(0.9, 0.9)) is None


def test_score_drop_improvement():
    assert detect_score_drop(_pts(0.7, 0.8)) is None


def test_score_drop_prev_zero():
    assert detect_score_drop(_pts(0.0, 0.5)) is None


def test_score_drop_single_exam():
    assert detect_score_drop(_pts(0.8)) is None


# ---------------- 2.3 detect_high_error_rate ----------------

def test_high_error_rate_warning():
    assert detect_high_error_rate(0.7) == "warning"
    assert detect_high_error_rate(0.8) == "warning"


def test_high_error_rate_info():
    assert detect_high_error_rate(0.5) == "info"
    assert detect_high_error_rate(0.6) == "info"


def test_high_error_rate_below():
    assert detect_high_error_rate(0.4) is None


def test_high_error_rate_no_answer():
    assert detect_high_error_rate(None) is None


# ---------------- 2.4 check_all_warnings 组装（DB） ----------------

def _setup(db_session, name="张三"):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    stu = Student(class_id=cls.id, name=name, created_at=NOW - datetime.timedelta(days=30))
    db_session.add(stu)
    db_session.flush()
    return stu


def _add_exam(db_session, student, exam_type, status, exam_date, answers):
    """answers：is_correct bool 列表。返回 exam。"""
    exam = ExamRecord(
        student_id=student.id,
        name="T",
        exam_type=exam_type,
        status=status,
        exam_date=exam_date,
    )
    db_session.add(exam)
    db_session.flush()
    for is_correct in answers:
        q = Question(content="c", answer="a", difficulty=Difficulty.easy)
        db_session.add(q)
        db_session.flush()
        db_session.add(
            StudentAnswer(
                student_id=student.id,
                question_id=q.id,
                exam_id=exam.id,
                is_correct=is_correct,
                answered_at=datetime.datetime.combine(exam_date, datetime.time(10, 0)),
            )
        )
    db_session.flush()
    return exam


def _stale_student(db_session, name="张三"):
    """最近作答在 3 天前 → 命中 no_login。"""
    stu = _setup(db_session, name)
    _add_exam(db_session, stu, ExamType.practice, ExamStatus.published,
              (NOW - datetime.timedelta(days=5)).date(), [True, True])
    db_session.commit()
    return stu


def test_check_all_warnings_creates_no_login(db_session):
    _stale_student(db_session)
    service = EarlyWarningService(db_session)
    summary = service.check_all_warnings(now=NOW)
    assert summary["total"] == 1
    assert summary["created"] == 1
    assert summary["failed"] == 0
    assert summary["by_type"].get("no_login") == 1
    warning = db_session.query(WarningLog).one()
    assert warning.student_id == 1
    assert warning.warning_type.value == "no_login"
    assert warning.level.value == "warning"
    assert warning.status.value == "pending"
    assert warning.data["days"] >= 3


def test_check_all_warnings_dedup(db_session):
    _stale_student(db_session)
    service = EarlyWarningService(db_session)
    first = service.check_all_warnings(now=NOW)
    second = service.check_all_warnings(now=NOW)
    assert first["created"] == 1
    assert second["created"] == 0  # 同生同类型 pending 去重
    assert db_session.query(WarningLog).count() == 1


def test_check_all_warnings_single_failure(db_session, monkeypatch):
    _stale_student(db_session, "张三")  # id=1，首生检测抛异常
    _stale_student(db_session, "李四")  # id=2，正常创建
    db_session.commit()
    service = EarlyWarningService(db_session)
    calls = {"n": 0}
    original = service._last_exercise_at

    def _boom(student_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(student_id)

    monkeypatch.setattr(service, "_last_exercise_at", _boom)
    summary = service.check_all_warnings(now=NOW)
    assert summary["failed"] == 1
    assert summary["created"] == 1  # 第二个学生仍正常创建


def test_check_student_score_drop_creates(db_session):
    stu = _setup(db_session)
    _add_exam(db_session, stu, ExamType.exam, ExamStatus.completed,
              (NOW - datetime.timedelta(days=6)).date(), [True, True, True])
    _add_exam(db_session, stu, ExamType.exam, ExamStatus.completed,
              (NOW - datetime.timedelta(days=1)).date(), [False, True, True])
    db_session.commit()
    service = EarlyWarningService(db_session)
    result = service.check_student(stu, now=NOW)
    assert result["created"] == 1
    assert result["by_type"].get("score_drop") == 1
    warning = db_session.query(WarningLog).one()
    assert warning.warning_type.value == "score_drop"
    assert warning.level.value == "critical"  # 0.667 → critical


def test_check_student_high_error_rate_creates(db_session):
    stu = _setup(db_session)
    _add_exam(db_session, stu, ExamType.practice, ExamStatus.published,
              (NOW - datetime.timedelta(days=1)).date(), [False, False, False, True])
    db_session.commit()
    service = EarlyWarningService(db_session)
    result = service.check_student(stu, now=NOW)
    assert result["created"] == 1
    assert result["by_type"].get("high_error_rate") == 1
    warning = db_session.query(WarningLog).one()
    assert warning.warning_type.value == "high_error_rate"
    assert warning.level.value == "warning"  # 0.75 >= 0.7


def test_check_student_no_hit(db_session):
    stu = _setup(db_session)
    _add_exam(db_session, stu, ExamType.practice, ExamStatus.published,
              (NOW - datetime.timedelta(days=1)).date(), [True, True, True, True])
    db_session.commit()
    service = EarlyWarningService(db_session)
    result = service.check_student(stu, now=NOW)
    assert result["created"] == 0
    assert db_session.query(WarningLog).count() == 0
