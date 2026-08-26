"""预警链模型测试：WarningLog（1.1）。"""
import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Class, Grade, School, Student, WarningLog
from app.db.models.enums import WarningLevel, WarningStatus, WarningType


def _student(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    stu = Student(class_id=cls.id, name="张三")
    db_session.add(stu)
    db_session.flush()
    return stu


def _warning(db_session, **kw):
    stu = _student(db_session)
    warning = WarningLog(
        student_id=stu.id,
        warning_type=WarningType.no_login,
        level=WarningLevel.warning,
        title="连续未登录预警",
        **kw,
    )
    db_session.add(warning)
    db_session.flush()
    return warning, stu


def test_warning_log_defaults(db_session):
    w, _ = _warning(db_session)
    db_session.commit()
    got = db_session.get(WarningLog, w.id)
    assert got.warning_type == WarningType.no_login
    assert got.level == WarningLevel.warning
    assert got.status == WarningStatus.pending
    assert got.content == ""
    assert got.data == {}
    assert got.processed_by is None
    assert got.processed_at is None
    assert got.processed_note is None
    assert got.notified_teacher is False
    assert got.notified_parent is False
    assert got.notified_student is False
    assert got.created_at is not None


def test_warning_log_fields_roundtrip(db_session):
    w, _ = _warning(
        db_session,
        content="张三已连续 5 天未登录",
        data={"days": 5},
        status=WarningStatus.processed,
        processed_by=7,
        processed_at=datetime.datetime(2026, 8, 26, 12, 0, 0),
        processed_note="已电话联系家长",
    )
    db_session.commit()
    got = db_session.get(WarningLog, w.id)
    assert got.content == "张三已连续 5 天未登录"
    assert got.data == {"days": 5}
    assert got.status == WarningStatus.processed
    assert got.processed_by == 7
    assert got.processed_at == datetime.datetime(2026, 8, 26, 12, 0, 0)
    assert got.processed_note == "已电话联系家长"


def test_warning_log_enum_members_roundtrip(db_session):
    """三种类型/级别/状态成员组合可落库并回读。"""
    stu = _student(db_session)
    for wtype, wlevel in (
        (WarningType.no_login, WarningLevel.warning),
        (WarningType.score_drop, WarningLevel.critical),
        (WarningType.high_error_rate, WarningLevel.info),
    ):
        db_session.add(
            WarningLog(
                student_id=stu.id,
                warning_type=wtype,
                level=wlevel,
                title="t",
            )
        )
    db_session.commit()
    assert db_session.query(WarningLog).count() == 3
    got = db_session.query(WarningLog).filter_by(
        warning_type=WarningType.score_drop
    ).first()
    assert got.level == WarningLevel.critical
    assert got.status == WarningStatus.pending


def test_warning_log_requires_student(db_session):
    with pytest.raises(IntegrityError):
        db_session.add(
            WarningLog(
                warning_type=WarningType.no_login,
                level=WarningLevel.warning,
                title="t",
            )
        )
        db_session.flush()
