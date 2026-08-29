"""家长报告组工具单测（tasks 4.3）：生成预览不写通知 / 发送写通知 / 审批阻塞 / parent 403。"""
import asyncio

import pytest

from app.agents.tools import tools_parent_report
from app.agents.tools.context import ToolContext
from app.agents.tools.registry import execute_tool
from app.core.exceptions import ForbiddenError
from app.db.models import (
    Class,
    Grade,
    NotificationType,
    Parent,
    ParentNotification,
    School,
    Student,
    StudentParentBinding,
)


def run(coro):
    return asyncio.run(coro)


def _org(db):
    school = School(name="测试中学")
    db.add(school)
    db.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(grade)
    db.flush()
    cls = Class(grade_id=grade.id, name="高一(1)班", head_teacher="张老师")
    db.add(cls)
    db.flush()
    return school, cls


def _student(db, cls, name="张三", student_no="2023001234"):
    s = Student(class_id=cls.id, name=name, student_no=student_no, barrier_profile={})
    db.add(s)
    db.flush()
    return s


def _parent(db, student, name="王妈妈", phone=None):
    p = Parent(name=name, phone=phone or f"1380000000{student.id}")
    db.add(p)
    db.flush()
    db.add(StudentParentBinding(parent_id=p.id, student_id=student.id, bind_code="123456", status="active"))
    db.flush()
    return p


def _ctx(db, role="teacher"):
    return ToolContext(db=db, user={"user_id": 1, "role": role, "school_id": 1})


def _fake_report(no_data=False):
    return {
        "summary": "本周完成了氧化还原专项练习，进步明显",
        "detail": "正确率从 60% 提升到 80%，概念理解更扎实",
        "advice": "建议每天复习 10 分钟化合价口诀",
        "no_data": no_data,
    }


# ---------------- 生成预览（不发送） ----------------

def test_generate_parent_report_preview_not_sent(db_session, monkeypatch):
    school, cls = _org(db_session)
    stu = _student(db_session, cls)
    db_session.commit()

    monkeypatch.setattr(tools_parent_report, "generate_weekly_report",
                        lambda db, student_id, client=None: _fake_report())
    out = tools_parent_report.generate_parent_report(_ctx(db_session), student_id=stu.id)
    assert out["student_id"] == stu.id
    assert out["name"] == "张三"
    assert out["requires_confirmation"] is True
    assert "氧化还原专项练习" in out["report_preview"]
    assert out["report_data"]["summary"]
    # 预览不写任何家长通知
    assert db_session.query(ParentNotification).count() == 0


def test_generate_parent_report_student_missing(db_session):
    school, _ = _org(db_session)
    db_session.commit()
    out = tools_parent_report.generate_parent_report(_ctx(db_session), student_id=99999)
    assert out["error"] == "not_found"


# ---------------- 发送（写通知） ----------------

def test_send_report_to_parent_writes_notifications(db_session):
    school, cls = _org(db_session)
    stu = _student(db_session, cls)
    _parent(db_session, stu, name="王妈妈", phone="13800000001")
    _parent(db_session, stu, name="李爸爸", phone="13800000002")
    db_session.commit()

    out = tools_parent_report.send_report_to_parent(
        _ctx(db_session), student_id=stu.id, report_data=_fake_report()
    )
    assert out["sent"] is True
    assert out["notified_parents"] == 2
    rows = db_session.query(ParentNotification).all()
    assert len(rows) == 2
    assert {r.notification_type for r in rows} == {NotificationType.weekly_report}
    assert all(r.parent_id in {p.id for p in db_session.query(Parent).all()} for r in rows)


def test_send_report_to_parent_empty_data_generates(db_session, monkeypatch):
    school, cls = _org(db_session)
    stu = _student(db_session, cls)
    _parent(db_session, stu)
    db_session.commit()

    monkeypatch.setattr(tools_parent_report, "generate_weekly_report",
                        lambda db, student_id, client=None: _fake_report())
    out = tools_parent_report.send_report_to_parent(_ctx(db_session), student_id=stu.id)
    assert out["notified_parents"] == 1
    row = db_session.query(ParentNotification).first()
    assert "氧化还原" in row.content


def test_send_report_to_parent_inactive_binding_not_notified(db_session):
    school, cls = _org(db_session)
    stu = _student(db_session, cls)
    p = Parent(name="王妈妈", phone="13800000009")
    db_session.add(p)
    db_session.flush()
    db_session.add(StudentParentBinding(parent_id=p.id, student_id=stu.id, bind_code="654321", status="inactive"))
    db_session.commit()

    out = tools_parent_report.send_report_to_parent(
        _ctx(db_session), student_id=stu.id, report_data=_fake_report()
    )
    assert out["notified_parents"] == 0
    assert db_session.query(ParentNotification).count() == 0


# ---------------- 角色门控 ----------------

def test_parent_report_parent_role_forbidden(db_session):
    school, cls = _org(db_session)
    stu = _student(db_session, cls)
    db_session.commit()

    ctx = _ctx(db_session, role="parent")
    with pytest.raises(ForbiddenError):
        tools_parent_report.generate_parent_report(ctx, student_id=stu.id)
    with pytest.raises(ForbiddenError):
        tools_parent_report.send_report_to_parent(ctx, student_id=stu.id, report_data={})


# ---------------- 审批门控（registry 集成） ----------------

def test_send_report_approval_blocked(db_session, monkeypatch):
    school, cls = _org(db_session)
    stu = _student(db_session, cls)
    db_session.commit()

    monkeypatch.setattr(tools_parent_report, "generate_weekly_report",
                        lambda db, student_id, client=None: _fake_report())
    ctx = _ctx(db_session)
    out = run(execute_tool(ctx, "send_report_to_parent", {"student_id": stu.id}))
    assert out["error"] == "requires_approval_blocked"
    assert out["requires_approval"] is True
    assert out["tool"] == "send_report_to_parent"
    # 审批后执行键通过（越审批层进入工具执行）
    ctx.safe_guard.mark_approved("send_report_to_parent", {"student_id": stu.id})
    out2 = run(execute_tool(ctx, "send_report_to_parent", {"student_id": stu.id}))
    assert out2.get("error") != "requires_approval_blocked"


def test_parent_report_prerequisite_via_registry(db_session):
    school, cls = _org(db_session)
    stu = _student(db_session, cls)
    db_session.commit()

    ctx = _ctx(db_session)
    for name in ("generate_parent_report", "send_report_to_parent"):
        out = run(execute_tool(ctx, name, {}))
        assert out["error"] == "missing_prerequisites"
