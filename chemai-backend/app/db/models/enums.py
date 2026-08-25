"""数据库枚举——一律英文代码存储，中文标签由 API 层映射（D2）。

SQLite 下以 VARCHAR + CHECK 约束落地（native_enum=False, create_constraint=True），
非法取值在写入时被数据库拒绝（IntegrityError）。
"""
import enum

from sqlalchemy import Enum as SAEnum


def DbEnum(enum_cls, **kw):
    """SQLite 兼容枚举列：VARCHAR + CHECK 约束。"""
    return SAEnum(enum_cls, native_enum=False, create_constraint=True, **kw)


class BarrierType(str, enum.Enum):
    """障碍类型（三维障碍模型）：概念理解 / 审题障碍 / 表述障碍。"""

    concept = "concept"
    reading = "reading"
    expression = "expression"


class ExamType(str, enum.Enum):
    """考试类型：正式考试 / 日常练习 / 作业。"""

    exam = "exam"
    practice = "practice"
    homework = "homework"


class QuestionSource(str, enum.Enum):
    """题目来源：AI 生成 / 手动录入 / 日常练习 / OCR 导入。"""

    ai = "ai"
    manual = "manual"
    practice = "practice"
    ocr = "ocr"


class AuditStatus(str, enum.Enum):
    """四维安全审核状态。"""

    passed = "passed"
    warning = "warning"
    blocked = "blocked"


class Difficulty(str, enum.Enum):
    """题目难度四级。"""

    easy = "easy"
    medium = "medium"
    hard = "hard"
    competition = "competition"


class ExamStatus(str, enum.Enum):
    """考试生命周期六态：草稿/已发布/进行中/阅卷/完成/归档（终态只读）。"""

    draft = "draft"
    published = "published"
    in_progress = "in_progress"
    grading = "grading"
    completed = "completed"
    archived = "archived"


class AccountRole(str, enum.Enum):
    """账户角色：权限矩阵覆盖 admin/dept_admin/subject_lead/teacher/student；parent 独立路径。"""

    admin = "admin"
    dept_admin = "dept_admin"
    subject_lead = "subject_lead"
    teacher = "teacher"
    student = "student"
    parent = "parent"


class TeacherStatus(str, enum.Enum):
    """教师入驻状态：待审核教师不可登录（F4）。"""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class TeacherSubRole(str, enum.Enum):
    """教师管理树子角色——仅表达层级/展示，不参与权限判定（T3）。"""

    admin = "admin"
    dept_admin = "dept_admin"
    subject_lead = "subject_lead"
    teacher = "teacher"


class ReviewLevel(str, enum.Enum):
    """间隔复习 6 级状态（艾宾浩斯）：1 天/3 天/7 天/14 天/30 天/不再安排。"""

    level1 = "level1"
    level2 = "level2"
    level3 = "level3"
    level4 = "level4"
    level5 = "level5"
    level6 = "level6"


class ReviewTaskStatus(str, enum.Enum):
    """复习任务状态。"""

    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    archived = "archived"


class ParentBindingStatus(str, enum.Enum):
    """亲子绑定状态。"""

    active = "active"
    pending = "pending"
    inactive = "inactive"


class ParentBindingRelation(str, enum.Enum):
    """亲子关系：父亲 / 母亲 / 其他监护人。"""

    father = "father"
    mother = "mother"
    guardian = "guardian"


class NotificationType(str, enum.Enum):
    """家长通知类型：学习报告 / 预警提醒 / 教师消息。"""

    report = "report"
    warning = "warning"
    message = "message"


class UploadSessionStatus(str, enum.Enum):
    """上传会话状态机：UPLOADED → PREVIEWING → READY → (IMPORTING→IMPORTED | GRADING→GRADED) → DONE；DISCARDED/ERROR 终态。"""

    uploaded = "uploaded"
    previewing = "previewing"
    ready = "ready"
    importing = "importing"
    imported = "imported"
    grading = "grading"
    graded = "graded"
    done = "done"
    discarded = "discarded"
    error = "error"


class OCRTaskStatus(str, enum.Enum):
    """OCR 任务状态机：pending → processing → done / failed。"""

    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"
