"""组织链模型：School → Grade → Class → Teacher/Student（6 中占 5，Account 见 account.py）。

删除策略（D8）：组织链是数据隔离边界，删除受限（有子记录则拒绝）。
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import DbEnum, TeacherStatus, TeacherSubRole


class School(Base):
    """学校：顶层组织容器，数据隔离的最高边界。"""

    __tablename__ = "school"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str] = mapped_column(String(100), default="")
    address: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(20), default="")
    current_semester: Mapped[str] = mapped_column(String(20), default="")

    grades: Mapped[list["Grade"]] = relationship(
        back_populates="school", cascade="all, delete-orphan"
    )
    teachers: Mapped[list["Teacher"]] = relationship(back_populates="school")


class Grade(Base):
    """年级：学校下的学年层级。"""

    __tablename__ = "grade"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(
        ForeignKey("school.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(20), nullable=False)

    school: Mapped["School"] = relationship(back_populates="grades")
    classes: Mapped[list["Class"]] = relationship(
        back_populates="grade", cascade="all, delete-orphan"
    )


class Class(Base):
    """班级：数据隔离的核心边界。"""

    __tablename__ = "class"

    id: Mapped[int] = mapped_column(primary_key=True)
    grade_id: Mapped[int] = mapped_column(
        ForeignKey("grade.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    head_teacher: Mapped[str] = mapped_column(String(50), default="")
    student_count: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(20), default="")  # 学段：高中/初中
    subject: Mapped[str] = mapped_column(String(20), default="化学")

    grade: Mapped["Grade"] = relationship(back_populates="classes")
    students: Mapped[list["Student"]] = relationship(
        back_populates="class_", cascade="all, delete-orphan"
    )


class Teacher(Base):
    """教师：归属学校，含入驻状态与管理子角色。"""

    __tablename__ = "teacher"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(
        ForeignKey("school.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    status: Mapped[TeacherStatus] = mapped_column(
        DbEnum(TeacherStatus), default=TeacherStatus.pending
    )
    sub_role: Mapped[TeacherSubRole] = mapped_column(
        DbEnum(TeacherSubRole), default=TeacherSubRole.teacher
    )

    school: Mapped["School"] = relationship(back_populates="teachers")


class Student(Base):
    """学生：系统核心实体，含障碍画像/学习计划 JSON 与 6 位家长绑定码。"""

    __tablename__ = "student"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(
        ForeignKey("class.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    barrier_profile: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    barrier_last_updated: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    barrier_frozen: Mapped[bool] = mapped_column(default=False)  # 教师 override 后冻结，聚合跳过
    learning_plan: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    bind_code: Mapped[str] = mapped_column(String(6), default="")

    class_: Mapped["Class"] = relationship(back_populates="students")
