"""家长链模型：Parent + StudentParentBinding + ParentNotification。

删除策略（D8）：解除绑定不删除家长与学生记录。
"""
from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import DbEnum, NotificationType, ParentBindingRelation, ParentBindingStatus


class Parent(Base):
    __tablename__ = "parent"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(100), default="")
    password_hash: Mapped[str] = mapped_column(String(256), default="")

    bindings: Mapped[list["StudentParentBinding"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )


class StudentParentBinding(Base):
    """家长与学生的绑定关系记录。"""

    __tablename__ = "student_parent_binding"
    __table_args__ = (
        UniqueConstraint("parent_id", "student_id", name="uq_binding_parent_student"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("parent.id", ondelete="RESTRICT"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.id", ondelete="RESTRICT"), nullable=False
    )
    bind_code: Mapped[str] = mapped_column(String(6), nullable=False)
    relation: Mapped[ParentBindingRelation] = mapped_column(
        DbEnum(ParentBindingRelation), default=ParentBindingRelation.guardian
    )
    status: Mapped[ParentBindingStatus] = mapped_column(
        DbEnum(ParentBindingStatus), default=ParentBindingStatus.active
    )

    parent: Mapped["Parent"] = relationship(back_populates="bindings")


class ParentNotification(Base):
    """推送给家长的通知。"""

    __tablename__ = "parent_notification"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    notification_type: Mapped[NotificationType] = mapped_column(
        DbEnum(NotificationType), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(String(2000), default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)

    parent: Mapped["Parent"] = relationship()
