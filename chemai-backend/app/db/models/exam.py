"""教学链模型：ExamRecord → Question → StudentAnswer。

删除策略（D8）：
- 删除 ExamRecord 级联删除 StudentAnswer；
- 删除 Question 受限（有作答则拒绝）。
"""
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, String
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import AuditStatus, BarrierType, DbEnum, Difficulty, ExamType, QuestionSource


class ExamRecord(Base):
    """一次考试/练习/作业记录，归属班级。"""

    __tablename__ = "exam_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("class.id", ondelete="RESTRICT"), nullable=False
    )
    exam_type: Mapped[ExamType] = mapped_column(
        DbEnum(ExamType), nullable=False
    )
    exam_date: Mapped[date] = mapped_column(Date, nullable=False)
    attendee_count: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )  # 错题统计 JSON

    class_: Mapped["Class"] = relationship()  # noqa: F821


class Question(Base):
    """一份完整化学试题。"""

    __tablename__ = "question"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(String(2000), nullable=False)
    options: Mapped[list] = mapped_column(
        MutableList.as_mutable(JSON), default=list
    )
    answer: Mapped[str] = mapped_column(String(1000), nullable=False)
    analysis: Mapped[str] = mapped_column(String(2000), default="")
    knowledge_points: Mapped[str] = mapped_column(String(500), default="")
    difficulty: Mapped[Difficulty] = mapped_column(
        DbEnum(Difficulty), nullable=False
    )
    source: Mapped[QuestionSource] = mapped_column(
        DbEnum(QuestionSource), default=QuestionSource.manual
    )
    audit_status: Mapped[AuditStatus] = mapped_column(
        DbEnum(AuditStatus), default=AuditStatus.passed
    )
    audit_report: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )  # 四维审核报告 JSON


class StudentAnswer(Base):
    """学生针对某题目的作答记录。"""

    __tablename__ = "student_answer"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.id", ondelete="RESTRICT"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("question.id", ondelete="RESTRICT"), nullable=False
    )
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exam_record.id", ondelete="CASCADE"), nullable=False
    )
    answer_text: Mapped[str] = mapped_column(String(2000), default="")
    is_correct: Mapped[bool] = mapped_column(default=False)
    barrier_type: Mapped[BarrierType | None] = mapped_column(
        DbEnum(BarrierType), nullable=True
    )
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_correct: Mapped[int] = mapped_column(Integer, default=0)
