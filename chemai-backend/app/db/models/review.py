"""复习链模型：ReviewTask → ReviewHistory。

- 复习历史以表落地（非 JSON），支撑遗忘曲线多行记录（D7/F3）。
- 删除 ReviewTask 级联删除 ReviewHistory（D8）。
- 三态状态机（design.md D4）：pending / overdue / done（终态）；升降级与到期时间由
  SpacedRepetitionEngine 维护。
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import DbEnum, ReviewLevel, ReviewTaskStatus


class ReviewTask(Base):
    """错题间隔复习任务，按艾宾浩斯 6 级状态安排。"""

    __tablename__ = "review_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.id", ondelete="RESTRICT"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("question.id", ondelete="RESTRICT"), nullable=False
    )
    review_level: Mapped[ReviewLevel] = mapped_column(
        DbEnum(ReviewLevel), default=ReviewLevel.level1
    )
    status: Mapped[ReviewTaskStatus] = mapped_column(
        DbEnum(ReviewTaskStatus), default=ReviewTaskStatus.pending
    )
    # 艾宾浩斯调度字段（design.md D4/D5）：首级当天用 next_review_at=创建时刻表达
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_studied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_correct: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_error: Mapped[int] = mapped_column(Integer, default=0)

    history: Mapped[list["ReviewHistory"]] = relationship(
        back_populates="review_task", cascade="all, delete-orphan"
    )


class ReviewHistory(Base):
    """一次复习的结果记录。"""

    __tablename__ = "review_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_task_id: Mapped[int] = mapped_column(
        ForeignKey("review_task.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    review_date: Mapped[date] = mapped_column(Date, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    review_task: Mapped["ReviewTask"] = relationship(back_populates="history")
