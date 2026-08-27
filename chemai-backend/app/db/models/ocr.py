"""OCR 链模型：UploadSession → OCRTask + StudentSubmission。

删除策略（D8）：删除 UploadSession 级联删除 OCRTask 与 StudentSubmission。
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.enums import DbEnum, OCRTaskStatus, UploadSessionStatus


class UploadSession(Base):
    """答题卡处理流程会话，追踪整批上传的状态。"""

    __tablename__ = "upload_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[UploadSessionStatus] = mapped_column(
        DbEnum(UploadSessionStatus), default=UploadSessionStatus.uploaded
    )
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=0)  # 乐观锁版本
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tasks: Mapped[list["OCRTask"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    submissions: Mapped[list["StudentSubmission"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class OCRTask(Base):
    """单张答题卡的识别与批改任务。"""

    __tablename__ = "ocr_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("upload_session.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(500), default="")  # 待识别文件路径（批量上传写入）
    status: Mapped[OCRTaskStatus] = mapped_column(
        DbEnum(OCRTaskStatus), default=OCRTaskStatus.pending
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(String(500), default="")

    session: Mapped["UploadSession"] = relationship(back_populates="tasks")


class StudentSubmission(Base):
    """一次考试中某学生答题卡的独立提交。"""

    __tablename__ = "student_submission"

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_record.id", ondelete="RESTRICT"), nullable=True
    )  # 模式2/3（无考试）可空
    session_id: Mapped[int] = mapped_column(
        ForeignKey("upload_session.id", ondelete="CASCADE"), nullable=False
    )
    image_path: Mapped[str] = mapped_column(String(500), default="")
    answer_list: Mapped[list] = mapped_column(
        MutableList.as_mutable(JSON), default=list
    )
    total_score: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["UploadSession"] = relationship(back_populates="submissions")
