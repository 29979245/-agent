"""19 个 SQLAlchemy 模型注册点。

按 5 组依赖链导入，确保 Base.metadata 收集全部模型：
组织链 6 + 教学链 5 + 复习链 2 + 家长链 3 + OCR 链 3。
"""
from app.db.models.enums import (
    AccountRole,
    AuditStatus,
    BarrierType,
    Difficulty,
    ExamStatus,
    ExamType,
    NotificationType,
    OCRTaskStatus,
    ParentBindingRelation,
    ParentBindingStatus,
    QuestionSource,
    ReviewLevel,
    ReviewTaskStatus,
    TeacherStatus,
    TeacherSubRole,
    UploadSessionStatus,
)
from app.db.models.account import Account
from app.db.models.diagnosis import BarrierConfig, BarrierOverrideLog
from app.db.models.exam import ExamRecord, Question, QuestionSet, QuestionSetItem, StudentAnswer
from app.db.models.ocr import OCRTask, StudentSubmission, UploadSession
from app.db.models.org import Class, Grade, School, Student, Teacher
from app.db.models.parent import Parent, ParentNotification, StudentParentBinding
from app.db.models.review import ReviewHistory, ReviewTask

__all__ = [
    # 组织链
    "School",
    "Grade",
    "Class",
    "Teacher",
    "Student",
    "Account",
    # 教学链
    "ExamRecord",
    "Question",
    "StudentAnswer",
    # 复习链
    "ReviewTask",
    "ReviewHistory",
    # 诊断链
    "BarrierConfig",
    "BarrierOverrideLog",
    # 家长链
    "Parent",
    "StudentParentBinding",
    "ParentNotification",
    # OCR 链
    "UploadSession",
    "OCRTask",
    "StudentSubmission",
    # 枚举
    "AccountRole",
    "AuditStatus",
    "BarrierType",
    "Difficulty",
    "ExamType",
    "NotificationType",
    "OCRTaskStatus",
    "ParentBindingRelation",
    "ParentBindingStatus",
    "QuestionSource",
    "ReviewLevel",
    "ReviewTaskStatus",
    "TeacherStatus",
    "TeacherSubRole",
    "UploadSessionStatus",
]
