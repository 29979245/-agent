"""统一账户模型。

身份链（D5）：一个账户 1:1 关联一种角色记录（Teacher/Student/Parent）。
- RBAC 权限判定的唯一事实来源是 Account.role（T3）。
- role_id 指向哪种角色记录由应用层不变量保证（T2），SQLite 无法外键约束多态引用。
"""
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.enums import AccountRole, DbEnum


class Account(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[AccountRole] = mapped_column(
        DbEnum(AccountRole), nullable=False
    )
    role_id: Mapped[int] = mapped_column(Integer, nullable=False)


# role → 目标角色模型（T3：RBAC 判定以 role 为准；T2：校验 role_id 指向类型一致）
_ROLE_TARGET = {
    AccountRole.admin: "teacher",
    AccountRole.dept_admin: "teacher",
    AccountRole.subject_lead: "teacher",
    AccountRole.teacher: "teacher",
    AccountRole.student: "student",
    AccountRole.parent: "parent",
}


def validate_account_role_target(session, account) -> bool:
    """应用层不变量：Account.role 与 role_id 指向的角色记录类型一致（SQLite 无法外键约束）。"""
    from app.db.models.org import Student, Teacher
    from app.db.models.parent import Parent

    target = _ROLE_TARGET[account.role]
    model = {"teacher": Teacher, "student": Student, "parent": Parent}[target]
    return session.get(model, account.role_id) is not None
