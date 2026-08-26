"""预警去重（3.1）与家长通知管道（3.2）测试。"""
import datetime

from app.db.models import (
    Class,
    ExamRecord,
    Grade,
    Parent,
    ParentNotification,
    Question,
    School,
    Student,
    StudentAnswer,
    StudentParentBinding,
    WarningLog,
)
from app.db.models.enums import (
    Difficulty,
    ExamStatus,
    ExamType,
    NotificationType,
    ParentBindingRelation,
    ParentBindingStatus,
)
from app.services.analytics.early_warning import EarlyWarningService

NOW = datetime.datetime(2026, 8, 26, 12, 0, 0)
_phone_seed = [13800000000]


def _student(db_session, name="张三"):
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


def _answer(db_session, student, exam_type, status, exam_date, answers):
    exam = ExamRecord(
        student_id=student.id, name="T", exam_type=exam_type, status=status, exam_date=exam_date,
    )
    db_session.add(exam)
    db_session.flush()
    for is_correct in answers:
        q = Question(content="c", answer="a", difficulty=Difficulty.easy)
        db_session.add(q)
        db_session.flush()
        db_session.add(
            StudentAnswer(
                student_id=student.id, question_id=q.id, exam_id=exam.id,
                is_correct=is_correct,
                answered_at=datetime.datetime.combine(exam_date, datetime.time(10, 0)),
            )
        )
    db_session.flush()
    return exam


def _stale(db_session, name="张三"):
    """最近作答 5 天前 → 命中 no_login。"""
    stu = _student(db_session, name)
    _answer(db_session, stu, ExamType.practice, ExamStatus.published,
            (NOW - datetime.timedelta(days=5)).date(), [True])
    db_session.flush()
    return stu


def _binding(db_session, stu, name="王母"):
    _phone_seed[0] += 1
    parent = Parent(name=name, phone=str(_phone_seed[0]))
    db_session.add(parent)
    db_session.flush()
    b = StudentParentBinding(
        parent_id=parent.id, student_id=stu.id, bind_code="123456",
        relation=ParentBindingRelation.guardian, status=ParentBindingStatus.active,
    )
    db_session.add(b)
    db_session.flush()
    return parent, b


# ---------------- 3.1 去重 ----------------

def test_dedup_same_type_pending_skipped(db_session):
    stu = _stale(db_session)
    db_session.commit()
    svc = EarlyWarningService(db_session)
    assert svc.check_student(stu, now=NOW)["created"] == 1
    assert svc.check_student(stu, now=NOW)["created"] == 0  # 同类型 pending 去重
    assert db_session.query(WarningLog).count() == 1


def test_dedup_different_types_not_blocked(db_session):
    """已有 pending no_login 不阻断新建 score_drop（spec「不同类型互不影响」）。"""
    stu = _stale(db_session, "张三")
    db_session.commit()
    svc = EarlyWarningService(db_session)
    assert svc.check_student(stu, now=NOW)["created"] == 1  # no_login
    _answer(db_session, stu, ExamType.exam, ExamStatus.completed,
            (NOW - datetime.timedelta(days=4)).date(), [True, True, True])
    _answer(db_session, stu, ExamType.exam, ExamStatus.completed,
            (NOW - datetime.timedelta(days=2)).date(), [True, False, True])  # 错题率 1/3 不触发 high_error_rate
    db_session.commit()
    result = svc.check_student(stu, now=NOW)
    assert result["created"] == 1  # 仅 score_drop 新建
    types = {w.warning_type.value for w in db_session.query(WarningLog).all()}
    assert types == {"no_login", "score_drop"}


# ---------------- 3.2 家长通知管道 ----------------

def test_notify_active_binding_creates_notification(db_session):
    stu = _stale(db_session)
    parent, _ = _binding(db_session, stu)
    db_session.commit()
    svc = EarlyWarningService(db_session)
    result = svc.check_student(stu, now=NOW)
    assert result["created"] == 1
    assert result["notified"] == 1
    notif = db_session.query(ParentNotification).one()
    assert notif.parent_id == parent.id
    assert notif.notification_type == NotificationType.warning
    warning = db_session.query(WarningLog).one()
    assert warning.notified_parent is True
    assert warning.notified_teacher is True
    assert warning.notified_student is False


def test_notify_multiple_active_bindings(db_session):
    stu = _stale(db_session)
    _binding(db_session, stu, "父亲")
    _binding(db_session, stu, "母亲")
    db_session.commit()
    svc = EarlyWarningService(db_session)
    result = svc.check_student(stu, now=NOW)
    assert result["notified"] == 2
    assert db_session.query(ParentNotification).count() == 2


def test_notify_no_binding_silent(db_session):
    stu = _stale(db_session)
    db_session.commit()
    svc = EarlyWarningService(db_session)
    result = svc.check_student(stu, now=NOW)
    assert result["created"] == 1
    assert result["notified"] == 0
    assert db_session.query(ParentNotification).count() == 0
    warning = db_session.query(WarningLog).one()
    assert warning.notified_parent is False


def test_notify_inactive_binding_skipped(db_session):
    """inactive 绑定不通知（仅 active）。"""
    stu = _stale(db_session)
    parent = Parent(name="王母", phone=str(_phone_seed[0] + 1))
    db_session.add(parent)
    db_session.flush()
    db_session.add(
        StudentParentBinding(
            parent_id=parent.id, student_id=stu.id, bind_code="123456",
            relation=ParentBindingRelation.guardian, status=ParentBindingStatus.inactive,
        )
    )
    db_session.commit()
    svc = EarlyWarningService(db_session)
    result = svc.check_student(stu, now=NOW)
    assert result["notified"] == 0
    assert db_session.query(ParentNotification).count() == 0
