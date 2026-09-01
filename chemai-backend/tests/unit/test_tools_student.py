"""学生自助工具单测：我的错题 / 我的复习任务 / 我的学情。

学生角色缺省解析本人（Account.role_id → Student.id）；非学生 → ForbiddenError；
学生账号未绑定学生档案 → NotFoundError。
"""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.agents.tools import tools_student
from app.agents.tools.context import ToolContext
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models import (
    Account,
    AccountRole,
    Class,
    Difficulty,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Question,
    ReviewTask,
    School,
    Student,
    StudentAnswer,
)
from app.db.models.enums import ReviewLevel, ReviewTaskStatus


def run(coro):
    return asyncio.run(coro)


def _org(db):
    school = School(name="测试中学")
    db.add(school)
    db.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(grade)
    db.flush()
    cls = Class(grade_id=grade.id, name="高一(1)班", head_teacher="张老师")
    db.add(cls)
    db.flush()
    return school, grade, cls


def _student(db, cls, name="小明", profile=None):
    s = Student(
        class_id=cls.id, name=name,
        barrier_profile=profile or {"concept": 0.6, "reading": 0.3, "expression": 0.1},
    )
    db.add(s)
    db.flush()
    return s


def _account(db, role: AccountRole, role_id: int) -> Account:
    acc = Account(username=f"u_{role.value}_{role_id}", password_hash="x", role=role, role_id=role_id)
    db.add(acc)
    db.flush()
    return acc


def _ctx(db, user):
    return ToolContext(db=db, user=user)


def _student_ctx(db, student):
    acc = _account(db, AccountRole.student, student.id)
    return _ctx(db, {"user_id": acc.id, "role": "student"})


def _make_answer(db, student, cls, *, is_correct=False, answered_at=None):
    exam = ExamRecord(
        class_id=cls.id, student_id=student.id, name="练习",
        exam_type=ExamType.practice, status=ExamStatus.published,
        exam_date=datetime.utcnow().date(), attendee_count=0, stats={}, question_stats={},
    )
    db.add(exam)
    db.flush()
    q = Question(
        content="下列反应属于氧化还原反应的是？",
        options=["A. 复分解", "B. 置换反应", "C. 中和", "D. 沉淀"],
        answer="B", analysis="置换反应有化合价变化",
        knowledge_points="氧化还原反应", difficulty=Difficulty.medium,
    )
    db.add(q)
    db.flush()
    db.add(StudentAnswer(
        student_id=student.id, question_id=q.id, exam_id=exam.id,
        answer_text="A" if is_correct else "C", is_correct=is_correct,
        answered_at=answered_at or datetime.utcnow(),
    ))
    db.flush()
    return exam, q


# ---------------- show_my_wrong_questions ----------------

def test_show_my_wrong_questions_lists_own(db_session):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    _, q = _make_answer(db_session, stu, cls, is_correct=False)
    ctx = _student_ctx(db_session, stu)

    out = tools_student.show_my_wrong_questions(ctx)
    assert out["student_id"] == stu.id
    assert out["count"] == 1
    assert out["items"][0]["question_id"] == q.id
    assert out["items"][0]["error_count"] == 1
    assert out["items"][0]["your_answer"] == "C"


def test_show_my_wrong_questions_excludes_correct_and_mastered(db_session):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    _, correct_q = _make_answer(db_session, stu, cls, is_correct=True)
    _, wrong_q = _make_answer(db_session, stu, cls, is_correct=False)
    # 已掌握（ReviewTask done）的错题应移除
    db_session.add(ReviewTask(
        student_id=stu.id, question_id=wrong_q.id,
        review_level=ReviewLevel.level1, status=ReviewTaskStatus.done,
    ))
    db_session.commit()
    ctx = _student_ctx(db_session, stu)

    out = tools_student.show_my_wrong_questions(ctx)
    qids = {it["question_id"] for it in out["items"]}
    assert correct_q.id not in qids
    assert wrong_q.id not in qids
    assert out["count"] == 0


def test_show_my_wrong_questions_non_student_forbidden(db_session):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    _make_answer(db_session, stu, cls, is_correct=False)
    ctx = _ctx(db_session, {"user_id": 1, "role": "teacher"})
    with pytest.raises(ForbiddenError):
        tools_student.show_my_wrong_questions(ctx)


def test_show_my_wrong_questions_unbound_student_not_found(db_session):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    # 账号 role_id 未指向学生档案
    _account(db_session, AccountRole.student, role_id=99999)
    ctx = _ctx(db_session, {"user_id": 2, "role": "student"})
    with pytest.raises(NotFoundError):
        tools_student.show_my_wrong_questions(ctx)


# ---------------- show_my_review_tasks ----------------

def test_show_my_review_tasks_lists_due(db_session):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    _, q = _make_answer(db_session, stu, cls, is_correct=False)
    db_session.add(ReviewTask(
        student_id=stu.id, question_id=q.id,
        review_level=ReviewLevel.level1, status=ReviewTaskStatus.pending,
        next_review_at=datetime.utcnow() + timedelta(days=1),
    ))
    db_session.commit()
    ctx = _student_ctx(db_session, stu)

    out = tools_student.show_my_review_tasks(ctx)
    assert out["student_id"] == stu.id
    assert out["count"] == 1
    assert out["tasks"][0]["question_id"] == q.id
    assert out["tasks"][0]["content"] == q.content
    assert out["tasks"][0]["status"] == "pending"


def test_show_my_review_tasks_empty(db_session):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    ctx = _student_ctx(db_session, stu)
    out = tools_student.show_my_review_tasks(ctx)
    assert out["count"] == 0
    assert out["tasks"] == []


# ---------------- show_my_report ----------------

def test_show_my_report_returns_aggregate(db_session):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    _make_answer(db_session, stu, cls, is_correct=False)  # 1 错
    _make_answer(db_session, stu, cls, is_correct=True)   # 1 对
    ctx = _student_ctx(db_session, stu)

    out = tools_student.show_my_report(ctx)
    assert out["stats"]["completed_exercises"] == 2
    assert out["stats"]["accuracy"] == 0.5
    assert out["profile"]["name"] == "小明"


def test_show_my_report_no_data(db_session):
    school, _, cls = _org(db_session)
    stu = _student(db_session, cls)
    ctx = _student_ctx(db_session, stu)
    out = tools_student.show_my_report(ctx)
    assert out["stats"]["completed_exercises"] == 0
