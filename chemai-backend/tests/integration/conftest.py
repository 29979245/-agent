"""练习/复习/错题 API 集成测试共享夹具。

内存库 + StaticPool：请求会话与后台副作用会话共享同一连接，
覆盖 get_db / get_session_factory / get_side_effect_client，验证后台任务在提交后落库。
"""
import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.practice import get_side_effect_client, get_session_factory
from app.core.security import create_token
from app.db.base import Base
from app.db.models import (
    Account,
    Class,
    ExamRecord,
    Grade,
    Question,
    School,
    Student,
)
from app.db.models.enums import (
    AccountRole,
    AuditStatus,
    Difficulty,
    ExamStatus,
    ExamType,
    QuestionSource,
)
from app.db.session import get_db
from app.main import app


class _NoLLMClient:
    """后台诊断客户端桩：available=False → 走规则单路降级，不触发 LLM 重试。"""

    available = False


@pytest.fixture()
def exercise_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()

    def _override_db():
        yield session

    def _override_factory():
        yield factory

    def _override_client():
        return _NoLLMClient()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_session_factory] = _override_factory
    app.dependency_overrides[get_side_effect_client] = _override_client

    with TestClient(app) as c:
        yield c, session

    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


# ---------------- 种子数据 ----------------

def _student(db, name="张三", barrier=None):
    school = School(name="S")
    db.add(school)
    db.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(grade)
    db.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db.add(cls)
    db.flush()
    stu = Student(class_id=cls.id, name=name, barrier_profile=barrier or {})
    db.add(stu)
    db.flush()
    return stu


def _account(db, student):
    acc = Account(username=f"stu{student.id}", password_hash="x", role=AccountRole.student,
                  role_id=student.id)
    db.add(acc)
    db.flush()
    return acc


def _headers(account):
    token = create_token(user_id=account.id, role="student", school_id=1)
    return {"Authorization": f"Bearer {token}"}


def _practice_exam(db, student, questions=None, name="自适应练习", extra_stats=None):
    exam = ExamRecord(
        class_id=None,
        student_id=student.id,
        name=name,
        exam_type=ExamType.practice,
        status=ExamStatus.published,
        exam_date=datetime.date.today(),
        question_stats={"difficulty": "medium", "deadline": None, **(extra_stats or {})},
    )
    db.add(exam)
    db.flush()
    copied = []
    for i, q in enumerate(questions or []):
        copied.append(Question(
            content=q["content"], options=q.get("options", []), answer=q["answer"],
            analysis=q.get("analysis", ""), knowledge_points=q.get("kp", ""),
            difficulty=q.get("difficulty", Difficulty.medium), source=QuestionSource.practice,
            audit_status=AuditStatus.passed, audit_report={}, record_id=exam.id,
        ))
    db.add_all(copied)
    db.flush()
    db.commit()
    return exam
