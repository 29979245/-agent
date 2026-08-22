"""复习链模型测试：ReviewTask/ReviewHistory（4.1-4.2）。"""
import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Class, Grade, Question, ReviewHistory, ReviewTask, School, Student
from app.db.models.enums import Difficulty, ReviewLevel, ReviewTaskStatus


def _org(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    return cls


def _stu_question(db_session):
    cls = _org(db_session)
    stu = Student(class_id=cls.id, name="张三")
    db_session.add(stu)
    db_session.flush()
    q = Question(content="c", answer="a", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.flush()
    return stu, q


def _task(db_session, **kw):
    stu, q = _stu_question(db_session)
    task = ReviewTask(student_id=stu.id, question_id=q.id)
    db_session.add(task)
    db_session.flush()
    return task, stu, q


# ---- 4.1 ReviewTask ----

def test_review_task_defaults(db_session):
    task, _, _ = _task(db_session)
    db_session.commit()
    got = db_session.get(ReviewTask, task.id)
    assert got.review_level == ReviewLevel.level1
    assert got.status == ReviewTaskStatus.pending


def test_review_task_state_transition(db_session):
    task, _, _ = _task(db_session)
    task.review_level = ReviewLevel.level3
    task.status = ReviewTaskStatus.in_progress
    db_session.commit()
    got = db_session.get(ReviewTask, task.id)
    assert got.review_level == ReviewLevel.level3
    assert got.status == ReviewTaskStatus.in_progress


# ---- 4.2 ReviewHistory ----

def test_review_history_crud(db_session):
    task, _, _ = _task(db_session)
    hist = ReviewHistory(review_task_id=task.id, level=2, review_date=datetime.date(2026, 8, 22), passed=True)
    db_session.add(hist)
    db_session.commit()
    got = db_session.get(ReviewHistory, hist.id)
    assert got.passed is True
    assert got.review_task_id == task.id


def test_delete_review_task_cascades_history(db_session):
    task, _, _ = _task(db_session)
    db_session.add(ReviewHistory(review_task_id=task.id, level=1, review_date=datetime.date(2026, 8, 22), passed=True))
    db_session.flush()
    db_session.delete(task)
    db_session.commit()
    assert db_session.query(ReviewHistory).count() == 0


def test_review_history_requires_task(db_session):
    with pytest.raises(IntegrityError):
        db_session.add(ReviewHistory(level=1, review_date=datetime.date(2026, 8, 22), passed=True))
        db_session.flush()
