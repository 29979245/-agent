"""change parent-backend 3.6：数据权限隔离集成验证。

覆盖（L2）：
1) 全链路 HTTP：家长登录 → 绑定 → 子女列表 → 报告 → 周报 → 通知列表/已读。
2) 守卫不遗漏：未绑定 403 / 越权隔离 / 学生不存在 404 / 非家长令牌 403 /
   通知身份取自 token（忽略 query parent_id）。
"""
import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_token
from app.db.base import Base
from app.db.models import (
    Account,
    Class,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Parent,
    ParentNotification,
    Question,
    School,
    Student,
    StudentAnswer,
    StudentParentBinding,
)
from app.db.models.enums import (
    AccountRole,
    Difficulty,
    NotificationType,
    ParentBindingRelation,
    ParentBindingStatus,
)
from app.db.session import get_db
from app.main import app
from app.services.analytics import weekly_report_service as wrs

TODAY = datetime.date(2026, 8, 27)


def _at(day, hour=10):
    return datetime.datetime.combine(day, datetime.time(hour, 0))


@pytest.fixture()
def parent_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()

    def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c, session
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


class _MockLLM:
    responses = [
        '{"summary": "本周完成了练习，大部分题目都做对了。", '
        '"detail": "最近在学习和氧气相关的反应。", '
        '"advice": "可以和孩子聊聊生活中的化学。", "no_data": false}',
        '{"summary": "孩子最近状态不错，继续加油。", "no_data": false}',
    ]

    def complete(self, messages):
        return self.responses.pop(0)


@pytest.fixture()
def mock_llm(monkeypatch):
    mock = _MockLLM()
    monkeypatch.setattr(wrs, "FallbackDiagnosisLLMClient", lambda: mock)
    return mock


def _seed_student(session, name="张三", bind_code="123456"):
    school = School(name="S")
    session.add(school)
    session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    session.add(grade)
    session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    session.add(cls)
    session.flush()
    stu = Student(class_id=cls.id, name=name, bind_code=bind_code)
    session.add(stu)
    session.flush()
    q = Question(content="题目", answer="B", knowledge_points="氧化还原反应", difficulty=Difficulty.easy)
    session.add(q)
    session.flush()
    exam = ExamRecord(
        student_id=stu.id, name="每日练习", status=ExamStatus.published,
        exam_type=ExamType.practice, exam_date=TODAY, question_stats={"mode": "daily"},
    )
    session.add(exam)
    session.flush()
    session.add(
        StudentAnswer(
            student_id=stu.id, question_id=q.id, exam_id=exam.id,
            answer_text="B", is_correct=True, answered_at=_at(TODAY),
        )
    )
    session.flush()
    return stu


def _seed_parent(session, stu, phone="13900000001", bind_code="123456", name="张父"):
    parent = Parent(name=name, phone=phone)
    session.add(parent)
    session.flush()
    session.add(
        StudentParentBinding(
            parent_id=parent.id, student_id=stu.id, bind_code=bind_code,
            relation=ParentBindingRelation.father, status=ParentBindingStatus.active,
        )
    )
    session.flush()
    return parent


def _login(client, phone="13900000001", bind_code="123456"):
    resp = client.post(
        "/api/parent/login", json={"phone": phone, "bind_code": bind_code}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _parent_token(session, parent):
    account = Account(
        username=f"parent_{parent.id}", password_hash="x",
        role=AccountRole.parent, role_id=parent.id,
    )
    session.add(account)
    session.flush()
    return create_token(account.id, AccountRole.parent.value)


# ---------------- 全链路 ----------------

def test_parent_full_chain_http(parent_client, mock_llm):
    client, session = parent_client
    stu = _seed_student(session)
    _seed_parent(session, stu)
    note = ParentNotification(
        parent_id=session.query(Parent).first().id,
        notification_type=NotificationType.daily_report,
        title="张三今日练习已推送",
        content="内容",
    )
    session.add(note)
    session.flush()
    session.commit()

    token = _login(client)  # ① 家长登录
    # ② 子女列表
    children = client.get("/api/parent/children", headers=_auth(token)).json()["children"]
    assert [c["name"] for c in children] == ["张三"]
    # ③ 报告
    report = client.get(f"/api/parent/child/{stu.id}/report", headers=_auth(token))
    assert report.status_code == 200
    assert report.json()["student"]["name"] == "张三"
    assert "bind_code" not in str(report.json())
    # ④ 周报（懒生成）
    weekly = client.get(f"/api/parent/child/{stu.id}/weekly", headers=_auth(token))
    assert weekly.status_code == 200
    assert weekly.json()["summary"].startswith("本周完成了练习")
    # ⑤ 通知列表 + 已读
    notif = client.get("/api/parent/notifications", headers=_auth(token)).json()
    assert notif["total"] == 1
    nid = notif["notifications"][0]["id"]
    read = client.put(f"/api/parent/notifications/{nid}/read", headers=_auth(token))
    assert read.status_code == 200
    assert read.json()["is_read"] is True


# ---------------- 守卫不遗漏 ----------------

def test_all_child_endpoints_guard_unbound_403(parent_client, mock_llm):
    client, session = parent_client
    own_stu = _seed_student(session, name="张三")
    other_stu = _seed_student(session, name="李四", bind_code="654321")
    _seed_parent(session, own_stu)  # 家长仅绑定张三
    session.commit()
    token = _login(client)

    child_endpoints = [
        ("get", f"/api/parent/child/{other_stu.id}/report", None),
        ("get", f"/api/parent/child/{other_stu.id}/weekly", None),
        ("post", f"/api/parent/child/{other_stu.id}/weekly/generate", None),
        ("post", f"/api/parent/child/{other_stu.id}/report/ai-summary", None),
    ]
    for method, path, body in child_endpoints:
        resp = client.request(method, path, headers=_auth(token), json=body)
        assert resp.status_code == 403, (method, path)
        assert resp.json()["error_code"] == "BINDING_NOT_ACTIVE", (method, path)
        assert "李四" not in str(resp.json())  # 不泄露他人信息


def test_all_parent_endpoints_require_parent_role(parent_client):
    client, session = parent_client
    stu = _seed_student(session)
    _seed_parent(session, stu)
    session.commit()
    student_token = create_token(999, "student")  # 非家长令牌

    endpoints = [
        ("get", "/api/parent/children", None),
        ("get", f"/api/parent/child/{stu.id}/report", None),
        ("get", f"/api/parent/child/{stu.id}/weekly", None),
        ("get", "/api/parent/notifications", None),
    ]
    for method, path, body in endpoints:
        resp = client.request(method, path, headers=_auth(student_token), json=body)
        assert resp.status_code == 403, (method, path)
        assert resp.json()["error_code"] == "PARENT_ONLY", (method, path)


def test_child_student_not_found_404(parent_client):
    client, session = parent_client
    stu = _seed_student(session)
    _seed_parent(session, stu)
    session.commit()
    token = _login(client)
    resp = client.get("/api/parent/child/999999/report", headers=_auth(token))
    assert resp.status_code == 404


def test_notifications_identity_from_token(parent_client):
    client, session = parent_client
    stu = _seed_student(session)
    _seed_parent(session, stu)
    other = Parent(name="李母", phone="13900000002")
    session.add(other)
    session.flush()
    session.add(
        ParentNotification(
            parent_id=other.id, notification_type=NotificationType.reminder,
            title="他人通知", content="c",
        )
    )
    session.commit()
    token = _login(client)
    # query 带他人 parent_id 被忽略，仅返回 token 家长自己的通知（空）
    resp = client.get(f"/api/parent/notifications?parent_id={other.id}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_two_parents_bind_same_student(parent_client):
    """多个家长可绑定同一学生：两 active 绑定共存，各凭自己的绑定码登录并访问该生。"""
    client, session = parent_client
    stu = _seed_student(session, bind_code="123456")
    # 父1 先绑定（码 123456），学生端重新生成新码 654321 分享给母
    _seed_parent(session, stu, phone="13900000001", name="张父", bind_code="123456")
    _seed_parent(session, stu, phone="13900000002", name="李母", bind_code="654321")
    session.commit()
    assert (
        session.query(StudentParentBinding)
        .filter(
            StudentParentBinding.student_id == stu.id,
            StudentParentBinding.status == ParentBindingStatus.active,
        )
        .count()
        == 2
    )
    t1 = _login(client, phone="13900000001", bind_code="123456")
    t2 = _login(client, phone="13900000002", bind_code="654321")
    # 两个家长都能看到同一子女，且都能访问子女报告（绑定行级守卫各自通过）
    for token in (t1, t2):
        children = client.get("/api/parent/children", headers=_auth(token)).json()["children"]
        assert [c["name"] for c in children] == ["张三"]
        assert client.get(f"/api/parent/child/{stu.id}/report", headers=_auth(token)).status_code == 200
