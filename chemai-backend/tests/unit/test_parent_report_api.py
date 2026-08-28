"""change parent-backend 2.2/2.3：家长视角报告聚合 + 报告端点。

覆盖：统计卡/知识掌握/时间线字段齐全且响应不含 bind_code 与 teacher_comment；
绑定子女返回 / 未绑定 403 / 学生不存在 404。
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token, hash_password
from app.db.models import (
    Account,
    Class,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Parent,
    Question,
    School,
    Student,
    StudentAnswer,
    StudentParentBinding,
)
from app.db.models.enums import (
    AccountRole,
    Difficulty,
    ParentBindingRelation,
    ParentBindingStatus,
)
from app.db.session import get_db
from app.main import app
from app.services.analytics.parent_service import build_parent_report

TODAY = datetime.date(2026, 8, 27)  # 周四


def _at(day, hour=10):
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


def _student(db_session, env, name="张三"):
    stu = Student(class_id=env["class_id"], name=name, bind_code="ABCDEF")
    db_session.add(stu)
    db_session.flush()
    return stu


def _question(db_session, kps="氧化还原反应", answer="B"):
    q = Question(content="题目", answer=answer, knowledge_points=kps, difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.flush()
    return q


def _practice(db_session, stu, day):
    exam = ExamRecord(
        student_id=stu.id,
        name="每日练习",
        status=ExamStatus.published,
        exam_type=ExamType.practice,
        exam_date=day,
        question_stats={"mode": "daily"},
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


def _parent_account(db_session, phone="13900000001"):
    parent = Parent(name="张父", phone=phone)
    db_session.add(parent)
    db_session.flush()
    account = Account(
        username=f"parent_{parent.id}",
        password_hash=hash_password("x"),
        role=AccountRole.parent,
        role_id=parent.id,
    )
    db_session.add(account)
    db_session.flush()
    db_session.commit()
    return parent, account


def _bind(db_session, parent, student, status=ParentBindingStatus.active):
    binding = StudentParentBinding(
        parent_id=parent.id,
        student_id=student.id,
        bind_code="ABCDEF",
        relation=ParentBindingRelation.father,
        status=status,
    )
    db_session.add(binding)
    db_session.flush()
    db_session.commit()
    return binding


# ---------------- 2.2 家长视角聚合 ----------------

def test_parent_report_fields_complete(db_session, env):
    stu = _student(db_session, env)
    q1 = _question(db_session, kps="氧化还原反应")
    q2 = _question(db_session, kps="离子反应")
    exam = _practice(db_session, stu, TODAY)
    _answer(db_session, stu, exam, q1, TODAY, correct=True)
    _answer(db_session, stu, exam, q2, TODAY, correct=False)
    report = build_parent_report(db_session, stu.id, today=TODAY)
    # 字段齐全：统计卡/知识掌握/通俗特点/时间线
    assert set(report.keys()) == {"student", "stats", "knowledge_points", "learning_style", "timeline"}
    assert report["student"]["student_id"] == stu.id
    assert report["student"]["class_name"] == "1班"
    assert report["stats"]["weekly_exercises"] == 1
    assert report["stats"]["weekly_accuracy"] == 0.5
    assert report["stats"]["total_practices"] == 1
    assert report["stats"]["total_answers"] == 2
    assert report["stats"]["overall_accuracy"] == 0.5
    assert isinstance(report["learning_style"], str)
    assert len(report["timeline"]) == 1
    assert report["timeline"][0]["accuracy"] == 0.5


def test_parent_report_excludes_sensitive_fields(db_session, env):
    stu = _student(db_session, env)
    q = _question(db_session)
    exam = _practice(db_session, stu, TODAY)
    _answer(db_session, stu, exam, q, TODAY, correct=True)
    report = build_parent_report(db_session, stu.id, today=TODAY)
    # 不含 bind_code / 排名 / 教师评语
    assert "bind_code" not in report
    assert "teacher_comment" not in report
    assert "rank" not in report
    serialized = str(report)
    assert "ABCDEF" not in serialized  # bind_code 值不泄露


def test_parent_report_missing_student(db_session, env):
    _student(db_session, env)
    from app.core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        build_parent_report(db_session, 999, today=TODAY)


# ---------------- 2.3 报告端点 ----

@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    def _teardown():
        app.dependency_overrides.pop(get_db, None)

    with TestClient(app) as c:
        yield c
    _teardown()


def test_report_bound_child_returns(client, db_session, env):
    stu = _student(db_session, env)
    parent, account = _parent_account(db_session)
    _bind(db_session, parent, stu)
    resp = client.get(
        f"/api/parent/child/{stu.id}/report",
        headers={"Authorization": f"Bearer {create_token(account.id, AccountRole.parent.value)}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["student"]["name"] == "张三"
    assert "bind_code" not in str(body)
    assert "teacher_comment" not in str(body)


def test_report_unbound_403(client, db_session, env):
    stu = _student(db_session, env)
    bound_parent, _ = _parent_account(db_session)
    requester, requester_account = _parent_account(db_session, phone="13900000002")
    _bind(db_session, bound_parent, stu)  # 绑定在另一家长名下
    resp = client.get(
        f"/api/parent/child/{stu.id}/report",
        headers={"Authorization": f"Bearer {create_token(requester_account.id, AccountRole.parent.value)}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BINDING_NOT_ACTIVE"


def test_report_student_not_found(client, db_session, env):
    _student(db_session, env)
    parent, account = _parent_account(db_session)
    _bind(db_session, parent, _student(db_session, env, name="李四"))
    resp = client.get(
        "/api/parent/child/999999/report",
        headers={"Authorization": f"Bearer {create_token(account.id, AccountRole.parent.value)}"},
    )
    assert resp.status_code == 404
