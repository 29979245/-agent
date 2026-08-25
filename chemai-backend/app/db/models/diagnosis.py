"""诊断链模型：BarrierConfig（教师阈值配置）→ BarrierOverrideLog（覆盖操作日志）。

- BarrierConfig：教师诊断阈值配置，teacher_id 唯一，PUT 按此 upsert。
- BarrierOverrideLog：教师覆盖学生画像的留痕，含前后值/原因/时间戳。
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BarrierConfig(Base):
    """教师诊断阈值配置（doc 48 §10）：默认连续错误 3 / 连续正确 2 / 低分 3 / 预警 3 / 启用 false。"""

    __tablename__ = "barrier_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teacher.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    consecutive_error_threshold: Mapped[int] = mapped_column(Integer, default=3)
    consecutive_correct_threshold: Mapped[int] = mapped_column(Integer, default=2)
    low_score_threshold: Mapped[int] = mapped_column(Integer, default=3)
    warning_threshold: Mapped[int] = mapped_column(Integer, default=3)
    enabled: Mapped[bool] = mapped_column(default=False)


class BarrierOverrideLog(Base):
    """教师覆盖画像操作日志：覆盖前后画像、原因、操作教师与时间戳。"""

    __tablename__ = "barrier_override_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teacher.id", ondelete="RESTRICT"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.id", ondelete="CASCADE"), nullable=False
    )
    before: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    after: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
