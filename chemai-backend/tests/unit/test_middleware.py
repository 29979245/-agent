"""认证中间件（9.1）/ 统一错误码格式（9.2）/ 结构化日志（9.3）测试。"""
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.core.permissions import require_permission
from app.core.security import create_token, hash_password
from app.db.models import Account, Grade, School, Teacher
from app.db.models.enums import AccountRole, TeacherStatus
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def protected_client(db_session):
    """注册探测端点 + 覆盖 get_db + TestClient。"""

    @app.post("/api/_probe")
    @require_permission("exam", "create")
    def _probe(request: Request) -> dict:
        return {"ok": True}

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    for route in list(app.routes):
        if getattr(route, "path", None) == "/api/_probe":
            app.routes.remove(route)


def _teacher(db_session, status=TeacherStatus.approved):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    teacher = Teacher(school_id=school.id, name="王老师", phone="13800000001", status=status)
    db_session.add(teacher)
    db_session.flush()
    account = Account(
        username="t1",
        password_hash=hash_password("Passw0rd!"),
        role=AccountRole.teacher,
        role_id=teacher.id,
    )
    db_session.add(account)
    db_session.commit()
    return teacher, account


def _token(**overrides):
    return create_token(
        user_id=overrides.get("user_id", 1),
        role=overrides.get("role", "teacher"),
        school_id=overrides.get("school_id", 5),
        ttl=overrides.get("ttl", 3600),
    )


# ---- 9.1 中间件三场景 ----

def test_whitelist_path_passes_without_token(protected_client, db_session):
    resp = protected_client.get("/health")
    assert resp.status_code == 200


def test_missing_token_rejected(protected_client, db_session):
    resp = protected_client.post("/api/_probe")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTHENTICATION_REQUIRED"


def test_invalid_token_rejected(protected_client, db_session):
    resp = protected_client.post("/api/_probe", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "TOKEN_INVALID"


def test_expired_token_rejected(protected_client, db_session):
    token = _token(ttl=-60)
    resp = protected_client.post("/api/_probe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "TOKEN_EXPIRED"


def test_valid_token_passes(protected_client, db_session):
    token = _token(role="teacher")
    resp = protected_client.post("/api/_probe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---- 9.2 统一错误码格式 ----

def _assert_unified_format(body):
    assert set(body.keys()) == {"detail", "error_code", "suggestion"}
    assert isinstance(body["detail"], str)
    assert isinstance(body["error_code"], str)


def test_401_error_format(protected_client, db_session):
    resp = protected_client.post("/api/_probe")
    _assert_unified_format(resp.json())
    assert resp.json()["error_code"] == "AUTHENTICATION_REQUIRED"


def test_403_error_format(protected_client, db_session):
    token = _token(role="student")
    resp = protected_client.post("/api/_probe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    _assert_unified_format(resp.json())
    assert resp.json()["error_code"] == "PERMISSION_DENIED"


def test_404_error_format(protected_client, db_session):
    token = _token(role="teacher")
    resp = protected_client.get("/api/definitely-not-here", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404
    _assert_unified_format(resp.json())
    assert resp.json()["error_code"] == "NOT_FOUND"


def test_422_error_format(protected_client, db_session):
    resp = protected_client.post("/api/auth/login", json={})
    assert resp.status_code == 422
    _assert_unified_format(resp.json())
    assert resp.json()["error_code"] == "VALIDATION_ERROR"


# ---- 9.3 结构化日志 ----

def test_login_success_logged(protected_client, db_session, caplog):
    _teacher(db_session)
    with caplog.at_level("INFO", logger="chemai.auth"):
        resp = protected_client.post("/api/auth/login", json={"username": "t1", "password": "Passw0rd!"})
    assert resp.status_code == 200
    events = [r for r in caplog.records if r.event == "login_success"]
    assert len(events) == 1
    assert events[0].username == "t1"
    assert events[0].role == "teacher"


def test_login_failure_logged(protected_client, db_session, caplog):
    _teacher(db_session)
    with caplog.at_level("INFO", logger="chemai.auth"):
        resp = protected_client.post("/api/auth/login", json={"username": "t1", "password": "bad"})
    assert resp.status_code == 401
    events = [r for r in caplog.records if r.event == "login_failed"]
    assert len(events) == 1
    assert events[0].reason == "bad_credentials"


def test_refresh_logged(protected_client, db_session, caplog):
    _teacher(db_session)
    login = protected_client.post("/api/auth/login", json={"username": "t1", "password": "Passw0rd!"})
    refresh_token = login.json()["refresh_token"]
    with caplog.at_level("INFO", logger="chemai.auth"):
        resp = protected_client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    events = [r for r in caplog.records if r.event == "refresh_success"]
    assert len(events) == 1


def test_middleware_401_logged_with_reason(protected_client, db_session, caplog):
    with caplog.at_level("INFO", logger="chemai.guard"):
        protected_client.post("/api/_probe")  # 无 token
        protected_client.post("/api/_probe", headers={"Authorization": "Bearer garbage"})
    reasons = [r.reason for r in caplog.records if r.event == "auth_reject"]
    assert "missing_token" in reasons
    assert "token_invalid" in reasons
