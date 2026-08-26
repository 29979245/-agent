"""认证端点测试（7.4-7.5）：统一登录/刷新/家长登录。"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import decode_token, hash_password
from app.db.models import (
    Account,
    Class,
    Grade,
    Parent,
    School,
    Student,
    StudentParentBinding,
    Teacher,
)
from app.db.models.enums import (
    AccountRole,
    ParentBindingRelation,
    ParentBindingStatus,
    TeacherStatus,
)
from app.db.session import get_db
from app.main import app


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


def _org(db_session):
    school = School(name="测试学校", current_semester="2026-1")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    return school, grade, cls


def _teacher_account(db_session, school_id, status=TeacherStatus.approved, username="t1"):
    teacher = Teacher(school_id=school_id, name="王老师", phone="13800000001", status=status)
    db_session.add(teacher)
    db_session.flush()
    account = Account(
        username=username,
        password_hash=hash_password("Passw0rd!"),
        role=AccountRole.teacher,
        role_id=teacher.id,
    )
    db_session.add(account)
    db_session.flush()
    db_session.commit()
    return teacher, account


def _student_account(db_session, cls_id):
    student = Student(class_id=cls_id, name="张三", bind_code="123456")
    db_session.add(student)
    db_session.flush()
    account = Account(
        username="s1",
        password_hash=hash_password("Passw0rd!"),
        role=AccountRole.student,
        role_id=student.id,
    )
    db_session.add(account)
    db_session.flush()
    db_session.commit()
    return student, account


# ---- 7.4 统一登录 / 刷新 ----

def test_login_success_issues_tokens(client, db_session):
    school = _org(db_session)[0]
    _, account = _teacher_account(db_session, school.id)
    resp = client.post("/api/auth/login", json={"username": "t1", "password": "Passw0rd!"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["role"] == "teacher"
    assert body["user_id"] == account.id
    assert body["name"] == "王老师"
    assert body["school_id"] == school.id
    assert body["role_id"] == account.role_id


def test_login_wrong_password(client, db_session):
    _teacher_account(db_session, _org(db_session)[0].id)
    resp = client.post("/api/auth/login", json={"username": "t1", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "UNAUTHORIZED"


def test_login_unknown_username(client, db_session):
    resp = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "UNAUTHORIZED"


def test_refresh_returns_new_access(client, db_session):
    _teacher_account(db_session, _org(db_session)[0].id)
    login = client.post("/api/auth/login", json={"username": "t1", "password": "Passw0rd!"})
    refresh_token = login.json()["refresh_token"]
    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["role"] == "teacher"


def test_refresh_rejects_access_token(client, db_session):
    """access token 不可当 refresh 用（type 混淆，F6）。"""
    _teacher_account(db_session, _org(db_session)[0].id)
    login = client.post("/api/auth/login", json={"username": "t1", "password": "Passw0rd!"})
    access_token = login.json()["access_token"]
    resp = client.post("/api/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


def test_login_pending_teacher_rejected(client, db_session):
    school = _org(db_session)[0]
    _teacher_account(db_session, school.id, status=TeacherStatus.pending)
    resp = client.post("/api/auth/login", json={"username": "t1", "password": "Passw0rd!"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "ACCOUNT_PENDING"


def test_login_rejected_teacher_blocked(client, db_session):
    school = _org(db_session)[0]
    _teacher_account(db_session, school.id, status=TeacherStatus.rejected)
    resp = client.post("/api/auth/login", json={"username": "t1", "password": "Passw0rd!"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "ACCOUNT_REJECTED"


def test_student_login_resolves_school_id(client, db_session):
    _, grade, cls = _org(db_session)
    student, _ = _student_account(db_session, cls.id)
    resp = client.post("/api/auth/login", json={"username": "s1", "password": "Passw0rd!"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "student"
    assert resp.json()["school_id"] == grade.school_id
    assert resp.json()["role_id"] == student.id


# ---- 7.5 家长登录 ----

def _parent_chain(db_session, cls_id):
    parent = Parent(name="张父", phone="13900000001", email="p@example.com")
    db_session.add(parent)
    db_session.flush()
    student = Student(class_id=cls_id, name="张三", bind_code="654321")
    db_session.add(student)
    db_session.flush()
    binding = StudentParentBinding(
        parent_id=parent.id,
        student_id=student.id,
        bind_code="654321",
        relation=ParentBindingRelation.father,
        status=ParentBindingStatus.active,
    )
    db_session.add(binding)
    db_session.commit()
    return parent, student, binding


def test_parent_login_success(client, db_session):
    _, _, cls = _org(db_session)
    parent, _, _ = _parent_chain(db_session, cls.id)
    resp = client.post("/api/parent/login", json={"phone": "13900000001", "bind_code": "654321"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["role"] == "parent"
    assert body["name"] == "张父"
    assert body["school_id"] is None  # 家长令牌无学校 ID（D3）


def test_parent_login_wrong_bind_code(client, db_session):
    _, _, cls = _org(db_session)
    _parent_chain(db_session, cls.id)
    resp = client.post("/api/parent/login", json={"phone": "13900000001", "bind_code": "000000"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BIND_CODE_INVALID"


def test_parent_login_unknown_phone(client, db_session):
    resp = client.post("/api/parent/login", json={"phone": "13999999999", "bind_code": "654321"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BIND_CODE_INVALID"


def test_parent_token_no_school_id(client, db_session):
    """家长令牌 payload 不含 school_id（D3 / auth spec）。"""
    _, _, cls = _org(db_session)
    _parent_chain(db_session, cls.id)
    resp = client.post("/api/parent/login", json={"phone": "13900000001", "bind_code": "654321"})
    token = resp.json()["access_token"]
    payload = decode_token(token)
    assert payload["role"] == "parent"
    assert "school_id" not in payload
