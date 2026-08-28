"""教学链模型：ExamRecord → Question → StudentAnswer + 题库两级结构。

删除策略（D8）：
- 删除 ExamRecord 级联删除 StudentAnswer；
- 删除 Question 受限（有作答则拒绝）；
- 删除 QuestionSet 仅级联删 QuestionSetItem 关联、题目实体保留。

练习记录（ADR-0002）：练习/训练/每日练习按学生组织为 per-student ExamRecord，
student_id 非空、class_id 可空留白，与教师端 exam 六态流程隔离。
"""
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import (
    AuditStatus,
    BarrierType,
    DbEnum,
    Difficulty,
    ExamStatus,
    ExamType,
    QuestionSource,
)


class ExamRecord(Base):
    """一次考试/练习/作业记录；教师考试归属班级，学生练习按学生组织（ADR-0002）。"""

    __tablename__ = "exam_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("class.id", ondelete="RESTRICT"), nullable=True
    )
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("student.id", ondelete="RESTRICT"), nullable=True
    )  # per-student 练习/训练/每日练习记录归属（ADR-0002）
    name: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[ExamStatus] = mapped_column(
        DbEnum(ExamStatus), default=ExamStatus.draft
    )
    exam_type: Mapped[ExamType] = mapped_column(
        DbEnum(ExamType), nullable=False
    )
    exam_date: Mapped[date] = mapped_column(Date, nullable=False)
    attendee_count: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )  # 错题统计 JSON
    question_stats: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )  # 发布元数据 JSON：published/published_at/question_count/total_students；练习另含 difficulty/deadline

    class_: Mapped["Class"] = relationship()  # noqa: F821
    questions: Mapped[list["Question"]] = relationship(back_populates="exam")


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
    record_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_record.id", ondelete="SET NULL"), nullable=True
    )  # 考试关联：渠道一直接关联 / 渠道二复制后关联

    exam: Mapped["ExamRecord | None"] = relationship(back_populates="questions")


class QuestionSet(Base):
    """题库文件夹：两级题库的顶层组织单元（教师自建，is_preset 系统预设禁删）。"""

    __tablename__ = "question_set"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    teacher_id: Mapped[int] = mapped_column(Integer, default=0)
    region: Mapped[str] = mapped_column(String(100), default="")
    year: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(500), default="")
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    is_preset: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    items: Mapped[list["QuestionSetItem"]] = relationship(
        back_populates="question_set", cascade="all, delete-orphan"
    )


class QuestionSetItem(Base):
    """题库条目：题库文件夹与题目的多对多关联中间表。"""

    __tablename__ = "question_set_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey("question_set.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("question.id", ondelete="RESTRICT"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    question_set: Mapped["QuestionSet"] = relationship(back_populates="items")
    question: Mapped["Question"] = relationship()


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
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    barrier_type: Mapped[BarrierType | None] = mapped_column(
        DbEnum(BarrierType), nullable=True
    )
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_correct: Mapped[int] = mapped_column(Integer, default=0)
    # 平铺诊断列（评审 T2）：融合置信度 / 判定标志 / 版本 / 来源 / 详情
    fused_conf: Mapped[float | None] = mapped_column(nullable=True)
    rule_conf: Mapped[float | None] = mapped_column(nullable=True)
    llm_conf: Mapped[float | None] = mapped_column(nullable=True)
    diagnosis_flag: Mapped[str] = mapped_column(
        String(20), default="none"
    )  # none/manual_review/needs_attention/error
    diagnosis_version: Mapped[str] = mapped_column(String(20), default="")
    diagnosis_source: Mapped[str] = mapped_column(String(10), default="")  # rule/llm/fused/error
    diagnosis_detail: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )  # reasoning/suggestion/recommended_practice/error
