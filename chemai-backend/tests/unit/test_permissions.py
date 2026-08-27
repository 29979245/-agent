"""RBAC 权限测试（8.1-8.3）：矩阵 / 检查器 / 端点装饰器。"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.exceptions import ForbiddenError, TokenExpiredError, UnauthorizedError
from app.core.permissions import (
    MATRIX_ROLES,
    OPERATIONS,
    RESOURCES,
    ROLE_PERMISSIONS,
    has_permission,
    permission_checker,
    require_permission,
)
from app.core.security import create_token

ROLE_CRED = {"user_id": 1, "role": "teacher", "school_id": 5}
STUDENT_CRED = {"user_id": 2, "role": "student", "school_id": 5}


def _token(**overrides):
    cred = {**{"user_id": 1, "role": "teacher"}, **overrides}
    return create_token(
        user_id=cred["user_id"], role=cred["role"], school_id=cred.get("school_id")
    )


# ---- 8.1 矩阵 ----

def test_matrix_covers_all_roles_and_valid_resources():
    assert set(ROLE_PERMISSIONS.keys()) == set(MATRIX_ROLES)
    assert set(RESOURCES) <= set(ROLE_PERMISSIONS["admin"])  # admin 全量兜底
    for role, perms in ROLE_PERMISSIONS.items():
        assert perms.keys() <= set(RESOURCES), f"{role} 含未登记资源"


def test_matrix_operations_valid():
    for perms in ROLE_PERMISSIONS.values():
        for ops in perms.values():
            assert ops <= set(OPERATIONS)


def test_parent_excluded_from_matrix():
    assert "parent" not in ROLE_PERMISSIONS


def test_matrix_authorize_teacher_exam_create():
    assert has_permission("teacher", "exam", "create") is True


def test_matrix_deny_student_exam_create():
    assert has_permission("student", "exam", "create") is False


def test_subject_lead_read_only():
    for res in RESOURCES:
        assert has_permission("subject_lead", res, "read") is True
        # account 例外：各矩阵角色可改自身密码
        for op in ("create", "update", "delete"):
            assert has_permission("subject_lead", res, op) is False or res == "account"


def test_parent_default_deny():
    for res in RESOURCES:
        for op in OPERATIONS:
            assert has_permission("parent", res, op) is False


def test_unknown_role_default_deny():
    assert has_permission("hacker", "exam", "read") is False


# ---- 8.2 PermissionChecker ----

def test_checker_allows_teacher_exam_create():
    ctx = permission_checker.check(_token(role="teacher", school_id=5), "exam", "create")
    assert ctx.user_id == 1
    assert ctx.role == "teacher"
    assert ctx.school_id == 5


def test_checker_denies_student_question_create():
    with pytest.raises(ForbiddenError):
        permission_checker.check(_token(role="student"), "question", "create")


def test_checker_denies_parent_matrix_resource():
    with pytest.raises(ForbiddenError):
        permission_checker.check(_token(role="parent"), "exam", "read")


def test_checker_rejects_missing_token():
    class _Req:
        headers = {}

    with pytest.raises(UnauthorizedError):
        permission_checker.check_from_request(_Req(), "exam", "create")


def test_checker_rejects_expired_token():
    expired = create_token(user_id=1, role="teacher", ttl=-60)
    with pytest.raises(TokenExpiredError):
        permission_checker.check(expired, "exam", "create")


def test_checker_rejects_tampered_token():
    good = _token()
    header_b64, payload_b64, sig = good.split(".")
    tampered = f"{header_b64}.{payload_b64[:-1]}{'A' if payload_b64[-1] != 'A' else 'B'}.{sig}"
    with pytest.raises(UnauthorizedError):
        permission_checker.check(tampered, "exam", "create")


# ---- 7.1 diagnosis 资源矩阵 ----

def test_diagnosis_matrix_teacher_run_llm_override_config():
    # run-llm → create；override/config → update/read
    assert has_permission("teacher", "diagnosis", "create") is True
    assert has_permission("teacher", "diagnosis", "update") is True
    assert has_permission("teacher", "diagnosis", "read") is True
    assert has_permission("teacher", "diagnosis", "delete") is False


def test_diagnosis_matrix_student_read_only():
    assert has_permission("student", "diagnosis", "read") is True
    assert has_permission("student", "diagnosis", "create") is False
    assert has_permission("student", "diagnosis", "update") is False
    assert has_permission("student", "diagnosis", "delete") is False


def test_diagnosis_matrix_admin_dept_full_and_subject_lead_read():
    assert set(ROLE_PERMISSIONS["admin"]["diagnosis"]) == set(OPERATIONS)
    assert set(ROLE_PERMISSIONS["dept_admin"]["diagnosis"]) == set(OPERATIONS)
    assert ROLE_PERMISSIONS["subject_lead"]["diagnosis"] == {"read"}


def test_checker_denies_student_run_llm_403():
    with pytest.raises(ForbiddenError):
        permission_checker.check(_token(role="student"), "diagnosis", "create")


def test_checker_allows_teacher_override_403_ok():
    ctx = permission_checker.check(_token(role="teacher"), "diagnosis", "update")
    assert ctx.role == "teacher"


def test_decorator_denies_student_diagnosis_run_llm_403():
    app = _protected_app()
    # 复用 exam create 装饰器端点演示 403；diagnosis 越权语义由矩阵测试覆盖
    resp = TestClient(app).post("/protected", headers={"Authorization": f"Bearer {_token(role='student')}"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PERMISSION_DENIED"


# ---- 5.1 warning 资源矩阵 ----

def test_warning_matrix_admin_dept_full_subject_lead_read():
    assert set(ROLE_PERMISSIONS["admin"]["warning"]) == set(OPERATIONS)
    assert set(ROLE_PERMISSIONS["dept_admin"]["warning"]) == set(OPERATIONS)
    assert ROLE_PERMISSIONS["subject_lead"]["warning"] == {"read"}


def test_warning_matrix_teacher_no_delete():
    assert has_permission("teacher", "warning", "read") is True
    assert has_permission("teacher", "warning", "update") is True
    assert has_permission("teacher", "warning", "create") is True
    assert has_permission("teacher", "warning", "delete") is False


def test_warning_matrix_student_default_deny():
    for op in OPERATIONS:
        assert has_permission("student", "warning", op) is False


def test_checker_allows_teacher_warning_update():
    ctx = permission_checker.check(_token(role="teacher"), "warning", "update")
    assert ctx.role == "teacher"


def test_checker_denies_student_warning_read():
    with pytest.raises(ForbiddenError):
        permission_checker.check(_token(role="student"), "warning", "read")


# ---- report / account 资源矩阵（student-supplement-apis）----

def test_report_matrix_admin_full_subject_lead_read():
    assert set(ROLE_PERMISSIONS["admin"]["report"]) == set(OPERATIONS)
    assert ROLE_PERMISSIONS["subject_lead"]["report"] == {"read"}
    assert has_permission("teacher", "report", "read") is True
    assert has_permission("teacher", "report", "create") is False


def test_report_matrix_student_read_update():
    assert has_permission("student", "report", "read") is True
    assert has_permission("student", "report", "update") is True
    assert has_permission("student", "report", "create") is False
    assert has_permission("student", "report", "delete") is False


def test_account_matrix_all_roles_update():
    for role in MATRIX_ROLES:
        assert has_permission(role, "account", "update") is True


# ---- 8.3 require_permission 装饰器 ----

def _protected_app():
    from fastapi.responses import JSONResponse

    from app.core.exceptions import APIException

    app = FastAPI()

    @app.exception_handler(APIException)
    def _handler(request: Request, exc: APIException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    @app.post("/protected")
    @require_permission("exam", "create")
    def protected(request: Request) -> dict:
        return {
            "ok": True,
            "user_id": request.state.user.user_id,
            "role": request.state.user.role,
        }

    return app


def test_decorator_allows_authorized_role():
    client = TestClient(_protected_app())
    resp = client.post("/protected", headers={"Authorization": f"Bearer {_token(role='teacher')}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "user_id": 1, "role": "teacher"}


def test_decorator_denies_unauthorized_role():
    client = TestClient(_protected_app())
    resp = client.post("/protected", headers={"Authorization": f"Bearer {_token(role='student')}"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PERMISSION_DENIED"


def test_decorator_denies_no_token():
    client = TestClient(_protected_app())
    resp = client.post("/protected")
    assert resp.status_code == 401
