"""家长端认证守卫（design.md D1）。

parent 不入 RBAC 矩阵（permissions.py F2 default-deny），家长端点不走
require_permission，改用独立守卫：
- require_parent：解码 token → 断言 role=parent → 经 Account.role_id 解析 parent.id。
- require_bound_child：从路径参数取 student_id，校验存在 parent_id+student_id+status=active
  的绑定；无则 403"未绑定该学生"，学生记录不存在则 404。

通知类端点家长身份只取 token（require_parent），不信任 query 参数。
"""
from fastapi import Depends, Path, Request
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    TokenExpiredError as TokenExpiredAPIError,
    UnauthorizedError,
)
from app.core.security import InvalidTokenError, TokenExpiredError, decode_token
from app.db.models import Account, Parent, Student, StudentParentBinding
from app.db.models.enums import AccountRole, ParentBindingStatus
from app.db.session import get_db


def require_parent(request: Request, db: Session = Depends(get_db)) -> int:
    """家长身份守卫：解码 Bearer token → role=parent → 解析 parent.id。

    返回 parent.id 并挂到 request.state.parent_id 供端点与子守卫使用。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise UnauthorizedError(detail="缺少认证令牌", error_code="AUTHENTICATION_REQUIRED")
    token = auth[len("Bearer "):]
    try:
        payload = decode_token(token, expected_type="access")
    except TokenExpiredError:
        raise TokenExpiredAPIError()
    except InvalidTokenError:
        raise UnauthorizedError()
    if payload.get("role") != "parent":
        raise ForbiddenError(detail="仅家长可访问", error_code="PARENT_ONLY")
    account = db.get(Account, payload["user_id"])
    if account is None or account.role != AccountRole.parent:
        raise ForbiddenError(detail="仅家长可访问", error_code="PARENT_ONLY")
    parent = db.get(Parent, account.role_id)
    if parent is None:
        raise ForbiddenError(detail="仅家长可访问", error_code="PARENT_ONLY")
    request.state.parent_id = parent.id
    return parent.id


def require_bound_child(
    student_id: int = Path(..., description="学生 ID"),
    parent_id: int = Depends(require_parent),
    db: Session = Depends(get_db),
) -> None:
    """绑定行级守卫：以 require_parent 为子依赖，保证家长身份先解析。

    从路径参数取 student_id（FastAPI 依赖内声明 Path 即注入），校验
    parent_id + student_id + status=active 的绑定；学生不存在返回 404，
    绑定不生效返回 403"未绑定该学生"。
    """
    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError(detail="学生不存在", error_code="STUDENT_NOT_FOUND")
    binding = (
        db.query(StudentParentBinding)
        .filter(
            StudentParentBinding.parent_id == parent_id,
            StudentParentBinding.student_id == student_id,
            StudentParentBinding.status == ParentBindingStatus.active,
        )
        .first()
    )
    if binding is None:
        raise ForbiddenError(detail="未绑定该学生", error_code="BINDING_NOT_ACTIVE")
