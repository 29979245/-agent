"""RBAC 权限矩阵与检查器（8.1-8.3）。

ROLE_PERMISSIONS 是唯一权威数据源：5 角色（admin/dept_admin/subject_lead/teacher/student）
× 11 资源（school/grade/class/teacher/student/analysis/exam/question/ocr/grading/diagnosis）
× 4 操作。parent 不入矩阵——走独立认证路径，对矩阵资源默认拒绝（default-deny，F2）。
"""
import functools

from fastapi import Request

from app.core.exceptions import (
    ForbiddenError,
    TokenExpiredError as TokenExpiredAPIError,
    UnauthorizedError,
)
from app.core.security import InvalidTokenError, TokenExpiredError, decode_token

RESOURCES = [
    "school",
    "grade",
    "class",
    "teacher",
    "student",
    "analysis",
    "exam",
    "question",
    "ocr",
    "grading",
    "diagnosis",
]
OPERATIONS = ["create", "read", "update", "delete"]
MATRIX_ROLES = ["admin", "dept_admin", "subject_lead", "teacher", "student"]


def _full_crud(resources):
    return {r: set(OPERATIONS) for r in resources}


ROLE_PERMISSIONS: dict[str, dict[str, set[str]]] = {
    "admin": _full_crud(RESOURCES),
    "dept_admin": _full_crud(["school", "grade", "class", "teacher", "student"])
    | {
        "analysis": {"create", "read", "update"},
        "exam": {"create", "read", "update", "delete"},
        "question": {"create", "read", "update"},
        "ocr": {"read"},
        "grading": {"read"},
        "diagnosis": {"create", "read", "update", "delete"},
    },
    # 只读角色：学科组长对任何资源仅 read（F2）
    "subject_lead": {r: {"read"} for r in RESOURCES},
    "teacher": {
        "school": {"read"},
        "grade": {"read"},
        "class": {"read"},
        "teacher": {"read"},
        "student": {"create", "read", "update"},
        "analysis": {"read"},
        "exam": {"create", "read", "update", "delete"},
        "question": {"create", "read", "update"},
        "ocr": {"create", "read", "update"},
        "grading": {"create", "read", "update"},
        "diagnosis": {"create", "read", "update"},
    },
    "student": {
        "school": {"read"},
        "grade": {"read"},
        "class": {"read"},
        "teacher": set(),
        "student": set(),
        "analysis": {"read"},
        "exam": {"read"},
        "question": {"read"},
        "ocr": set(),
        "grading": set(),
        "diagnosis": {"read"},
    },
}


def has_permission(role: str, resource: str, action: str) -> bool:
    """矩阵查询：parent/未知角色/未知资源/未知操作一律拒绝（default-deny）。"""
    if role not in ROLE_PERMISSIONS:
        return False
    return action in ROLE_PERMISSIONS[role].get(resource, set())


class UserContext:
    """已认证用户上下文：从 JWT payload 提取，供端点与后续业务使用。"""

    def __init__(self, user_id: int, role: str, school_id: int | None = None):
        self.user_id = user_id
        self.role = role
        self.school_id = school_id

    def __repr__(self) -> str:
        return f"<UserContext user_id={self.user_id} role={self.role} school_id={self.school_id}>"


class PermissionChecker:
    """资源级检查：解码令牌 → 矩阵判定 → 用户上下文。"""

    def check(self, token: str, resource: str, action: str) -> UserContext:
        try:
            payload = decode_token(token, expected_type="access")
        except TokenExpiredError:
            raise TokenExpiredAPIError()
        except InvalidTokenError:
            raise UnauthorizedError()
        if not has_permission(payload["role"], resource, action):
            raise ForbiddenError()
        return UserContext(
            user_id=payload["user_id"],
            role=payload["role"],
            school_id=payload.get("school_id"),
        )

    def check_from_request(self, request: Request, resource: str, action: str) -> UserContext:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise UnauthorizedError()
        token = auth[len("Bearer "):]
        return self.check(token, resource, action)


permission_checker = PermissionChecker()


def require_permission(resource: str, action: str):
    """端点级权限声明装饰器。

    用法：
        @router.post("/exams")
        @require_permission("exam", "create")
        def create_exam(request: Request, ...):
            user = request.state.user  # UserContext
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(request: Request, *args, **kwargs):
            user = permission_checker.check_from_request(request, resource, action)
            request.state.user = user
            return func(request, *args, **kwargs)

        return wrapper

    return decorator
