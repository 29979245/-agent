"""预警链模型：WarningLog——预警引擎持久化实体（doc 31 §5.4）。

删除策略（D8）：预警属学情审计数据，student 删除受限（RESTRICT），保留审计线索。
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import DbEnum, WarningLevel, WarningStatus, WarningType


class WarningLog(Base):
    """一次预警记录：检测规则命中且通过去重后写入，教师经 /api/warning 处理闭环。"""

    __tablename__ = "warning_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.id", ondelete="RESTRICT"), nullable=False
    )
    warning_type: Mapped[WarningType] = mapped_column(
        DbEnum(WarningType), nullable=False
    )
    level: Mapped[WarningLevel] = mapped_column(DbEnum(WarningLevel), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(String(2000), default="")
    data: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )  # 结构化指标：未登录天数/降幅/错题率
    status: Mapped[WarningStatus] = mapped_column(
        DbEnum(WarningStatus), default=WarningStatus.pending
    )
    processed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processed_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notified_teacher: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_student: Mapped[bool] = mapped_column(Boolean, default=False)  # 学生端通知预留
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    student: Mapped["Student"] = relationship()  # noqa: F821
