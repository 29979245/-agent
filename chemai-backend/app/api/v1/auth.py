"""认证路由（7.4-7.5）：统一账户登录 / 令牌刷新 / 家长独立登录 / 修改密码。

- POST /api/auth/login           username + password → access + refresh
- POST /api/auth/refresh         refresh_token → 新 access + refresh
- POST /api/auth/change-password 校验旧密码后更新自身密码（account 资源）
- POST /api/parent/login         phone + bind_code → parent 令牌（无密码路径）
"""
import logging
import os

from fastapi import APIRouter, Depends, Request

auth_logger = logging.getLogger("chemai.auth")
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AccountPendingError,
    AccountRejectedError,
    BindCodeInvalidError,
    BusinessRuleViolationError,
    TokenExpiredError as TokenExpiredAPIError,
    UnauthorizedError,
)
from app.core.permissions import require_permission
from app.core.security import (
    REFRESH_TOKEN_TTL,
    InvalidTokenError,
    TokenExpiredError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.models import Account, Grade, Parent, Student, StudentParentBinding, Teacher
from app.db.models.enums import AccountRole, ParentBindingStatus, TeacherStatus
from app.db.session import get_db

auth_router = APIRouter()
parent_router = APIRouter()

_TEACHER_ROLES = {
    AccountRole.admin,
    AccountRole.dept_admin,
    AccountRole.subject_lead,
    AccountRole.teacher,
}


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ParentLoginRequest(BaseModel):
    phone: str
    bind_code: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6)


def _resolve_school_id(db: Session, account: Account) -> int | None:
    """解析账户归属学校：teacher 系取 Teacher.school_id；student 沿 class→grade→school 上溯。"""
    if account.role == AccountRole.student:
        from app.db.models import Class as ClassModel

        student = db.get(Student, account.role_id)
        if student is None:
            return None
        cls = db.get(ClassModel, student.class_id)
        if cls is None:
            return None
        grade = db.get(Grade, cls.grade_id)
        return grade.school_id if grade else None
    teacher = db.get(Teacher, account.role_id)
    return teacher.school_id if teacher else None


def _resolve_name(db: Session, account: Account) -> str:
    if account.role == AccountRole.student:
        stu = db.get(Student, account.role_id)
        return stu.name if stu else ""
    if account.role == AccountRole.parent:
        p = db.get(Parent, account.role_id)
        return p.name if p else ""
    teacher = db.get(Teacher, account.role_id)
    return teacher.name if teacher else ""


def _issue_tokens(
    account: Account, school_id: int | None, name: str
) -> dict:
    access = create_token(
        account.id, account.role.value, school_id=school_id, token_type="access"
    )
    refresh = create_token(
        account.id,
        account.role.value,
        school_id=school_id,
        token_type="refresh",
        ttl=REFRESH_TOKEN_TTL,
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user_id": account.id,
        "role": account.role.value,
        "name": name,
        "school_id": school_id,
        "role_id": account.role_id,
    }


@auth_router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict:
    """统一账户登录：教师/学生系走密码；教师待审核/被驳回拒绝。"""
    account = db.query(Account).filter(Account.username == payload.username).first()
    if account is None or not verify_password(payload.password, account.password_hash):
        auth_logger.info(
            "login_failed",
            extra={"event": "login_failed", "username": payload.username, "reason": "bad_credentials"},
        )
        raise UnauthorizedError()

    if account.role in _TEACHER_ROLES:
        teacher = db.get(Teacher, account.role_id)
        if teacher is None:
            raise UnauthorizedError()
        if teacher.status == TeacherStatus.pending:
            auth_logger.info(
                "login_rejected",
                extra={"event": "login_rejected", "username": payload.username, "role": "teacher", "reason": "pending"},
            )
            raise AccountPendingError()
        if teacher.status == TeacherStatus.rejected:
            auth_logger.info(
                "login_rejected",
                extra={"event": "login_rejected", "username": payload.username, "role": "teacher", "reason": "rejected"},
            )
            raise AccountRejectedError()

    school_id = None if account.role == AccountRole.parent else _resolve_school_id(db, account)
    name = _resolve_name(db, account)
    auth_logger.info(
        "login_success",
        extra={"event": "login_success", "username": payload.username, "role": account.role.value},
    )
    return _issue_tokens(account, school_id, name)


@auth_router.post("/change-password")
@require_permission("account", "update")
def change_password(
    request: Request, payload: ChangePasswordRequest, db: Session = Depends(get_db)
) -> dict:
    """修改自身密码：校验旧密码后以不可逆散列更新，任何矩阵角色可改自身。"""
    account = db.get(Account, request.state.user.user_id)
    if account is None or not verify_password(payload.old_password, account.password_hash):
        raise BusinessRuleViolationError(detail="旧密码不正确", error_code="BUSINESS_RULE_VIOLATION")
    account.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"detail": "密码修改成功", "success": True}


@auth_router.post("/refresh")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> dict:
    """用 refresh token 换取新 access + refresh（无状态，不存会话）。"""
    try:
        data = decode_token(payload.refresh_token, expected_type="refresh")
    except TokenExpiredError:
        auth_logger.info(
            "refresh_failed",
            extra={"event": "refresh_failed", "reason": "token_expired"},
        )
        raise TokenExpiredAPIError()
    except InvalidTokenError:
        auth_logger.info(
            "refresh_failed",
            extra={"event": "refresh_failed", "reason": "token_invalid"},
        )
        raise UnauthorizedError()
    account = db.get(Account, data["user_id"])
    if account is None:
        raise UnauthorizedError()
    school_id = None if account.role == AccountRole.parent else _resolve_school_id(db, account)
    name = _resolve_name(db, account)
    auth_logger.info(
        "refresh_success",
        extra={"event": "refresh_success", "user_id": account.id, "role": account.role.value},
    )
    return _issue_tokens(account, school_id, name)


def _get_or_create_parent_account(db: Session, parent: Parent) -> Account:
    account = (
        db.query(Account)
        .filter(Account.role == AccountRole.parent, Account.role_id == parent.id)
        .first()
    )
    if account is None:
        # 家长无密码登录，account 仅作令牌载体，password_hash 存随机值（永不用于校验）
        account = Account(
            username=f"parent_{parent.id}",
            password_hash=hash_password(os.urandom(16).hex()),
            role=AccountRole.parent,
            role_id=parent.id,
        )
        db.add(account)
        db.flush()
    return account


@parent_router.post("/login")
def parent_login(payload: ParentLoginRequest, db: Session = Depends(get_db)) -> dict:
    """家长独立登录：phone + bind_code，不校验密码。"""
    parent = db.query(Parent).filter(Parent.phone == payload.phone).first()
    if parent is None:
        raise BindCodeInvalidError()
    binding = (
        db.query(StudentParentBinding)
        .filter(
            StudentParentBinding.parent_id == parent.id,
            StudentParentBinding.bind_code == payload.bind_code,
            StudentParentBinding.status == ParentBindingStatus.active,
        )
        .first()
    )
    if binding is None:
        raise BindCodeInvalidError()

    account = _get_or_create_parent_account(db, parent)
    db.commit()
    return _issue_tokens(account, None, parent.name)
