"""诊断组 7 工具单测（tasks 4.x）：障碍诊断 / 诊断面板 / 学生列表 / 周报 / 自适应练习 / 学习计划。"""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.agents.tools import tools_diagnosis
from app.agents.tools.context import ToolContext
from app.core.exceptions import ForbiddenError
from app.db.models import (
    AuditStatus,
    Class,
    Difficulty,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Parent,
    Question,
    QuestionSource,
    School,
    Student,
    StudentAnswer,
    StudentParentBinding,
)


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    def __init__(self, text=None):
        self._text = text or '{"summary": "本周表现良好", "detail": "概念理解扎实", "advice": "继续练习", "no_data": false}'

    async def acomplete_chain(self, messages):
        return self._text

    def complete_chain(self, messages):
        return self._text


# ---------------- 造数 ----------------

def _make_org(db):
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


def _make_student(db, cls, name="小明", profile=None, **kw):
    s = Student(
        class_id=cls.id, name=name,
        barrier_profile=profile or {"concept": 0.7, "reading": 0.2, "expression": 0.1},
        **kw,
    )
    db.add(s)
    db.flush()
    return s


def _make_exam_answer(db, student, cls, today=None, is_correct=True):
    today = today or datetime.utcnow().date()
    exam = ExamRecord(
        class_id=cls.id, student_id=student.id, name="练习",
        exam_type=ExamType.practice, status=ExamStatus.published,
        exam_date=today, attendee_count=0, stats={}, question_stats={},
    )
    db.add(exam)
    db.flush()
    q = Question(
        content="H2O 的组成", options=[], answer="A", analysis="x",
        knowledge_points="水", difficulty=Difficulty.easy,
        source=QuestionSource.practice, audit_status=AuditStatus.passed, audit_report={},
        record_id=exam.id,
    )
    db.add(q)
    db.flush()
    db.add(StudentAnswer(
        exam_id=exam.id, student_id=student.id, question_id=q.id,
        answer_text="A", is_correct=is_correct, answered_at=datetime.utcnow(),
    ))
    db.flush()
    return exam


def _teacher_ctx(db, **kw):
    return ToolContext(db=db, user={"user_id": 1, "role": "teacher"}, **kw)


# ---------------- diagnose_barrier ----------------

def test_diagnose_barrier_individual(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    out = run(tools_diagnosis.diagnose_barrier(_teacher_ctx(db_session), student_id=s.id))
    assert out["student_id"] == s.id
    assert out["name"] == "小明"
    assert set(out["profile"]) == {"concept", "reading", "expression"}
    assert out["dominant_barrier"] == "concept"
    assert out["_component"] == "diagnosis-chart"


def test_diagnose_barrier_by_name_candidates(db_session):
    _, _, cls = _make_org(db_session)
    _make_student(db_session, cls, name="小明")
    _make_student(db_session, cls, name="小红")
    out = run(tools_diagnosis.diagnose_barrier(_teacher_ctx(db_session), student_name="小"))
    assert "candidates" in out
    assert out["total_candidates"] == 2


def test_diagnose_barrier_class_distribution(db_session):
    _, _, cls = _make_org(db_session)
    _make_student(db_session, cls, name="小明", profile={"concept": 0.8, "reading": 0.1, "expression": 0.1})
    _make_student(db_session, cls, name="小红", profile={"concept": 0.2, "reading": 0.6, "expression": 0.2})
    out = run(tools_diagnosis.diagnose_barrier(_teacher_ctx(db_session), class_id=cls.id))
    assert out["class_id"] == cls.id
    assert out["total_students"] == 2
    assert set(out["distribution"]) == {"concept", "reading", "expression"}
    # 均值画像：(0.8+0.2)/2=0.5, (0.1+0.6)/2=0.35, (0.1+0.2)/2=0.15
    assert out["distribution"] == {"concept": 0.5, "reading": 0.35, "expression": 0.15}
    assert out["students"][0]["dominant_barrier"] == "concept"
    assert out["students"][1]["dominant_barrier"] == "reading"


def test_diagnose_barrier_parent_privacy(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls, name="小明")
    other = _make_student(db_session, cls, name="其他学生")
    parent = Parent(name="明爸", phone="13800000000")
    db_session.add(parent)
    db_session.flush()
    db_session.add(StudentParentBinding(parent_id=parent.id, student_id=s.id, bind_code="123456", status="active"))
    db_session.flush()
    parent_ctx = ToolContext(db=db_session, user={"user_id": parent.id, "role": "parent"})
    # 自己的孩子在允许范围
    ok = run(tools_diagnosis.diagnose_barrier(parent_ctx, student_id=s.id))
    assert ok["student_id"] == s.id
    # 越权访问其他学生 → 工具直接调用抛 ForbiddenError（registry 包装转为 error dict）
    with pytest.raises(ForbiddenError):
        run(tools_diagnosis.diagnose_barrier(parent_ctx, student_id=other.id))


def test_diagnose_barrier_parent_class_forbidden(db_session):
    """家长携带班级参数（含 class_id 或 class_name）→ ForbiddenError（doc 30 §4.2）。"""
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls, name="小明")
    parent = Parent(name="明爸", phone="13800000002")
    db_session.add(parent)
    db_session.flush()
    db_session.add(StudentParentBinding(parent_id=parent.id, student_id=s.id, bind_code="123456", status="active"))
    db_session.flush()
    parent_ctx = ToolContext(db=db_session, user={"user_id": parent.id, "role": "parent"})
    # 纯班级参数
    with pytest.raises(ForbiddenError):
        run(tools_diagnosis.diagnose_barrier(parent_ctx, class_id=cls.id))
    with pytest.raises(ForbiddenError):
        run(tools_diagnosis.diagnose_barrier(parent_ctx, class_name="高一(1)班"))
    # student + class 双参数：class 即越权意图，同样拒绝
    with pytest.raises(ForbiddenError):
        run(tools_diagnosis.diagnose_barrier(parent_ctx, student_id=s.id, class_id=cls.id))


def test_diagnose_barrier_teacher_class_ok(db_session):
    """教师携带班级参数正常返回班级分布（家长门控不误伤教师）。"""
    _, _, cls = _make_org(db_session)
    _make_student(db_session, cls, name="小明")
    out = run(tools_diagnosis.diagnose_barrier(_teacher_ctx(db_session), class_id=cls.id))
    assert out["class_id"] == cls.id
    assert out["total_students"] == 1


def test_show_diagnosis_uses_panel_component(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    out = run(tools_diagnosis.show_diagnosis(_teacher_ctx(db_session), student_id=s.id))
    assert out["_component"] == "diagnosis-panel"


# ---------------- show_students ----------------

def test_show_students_classes_mode(db_session):
    _, _, cls = _make_org(db_session)
    out = run(tools_diagnosis.show_students(_teacher_ctx(db_session)))
    assert out["mode"] == "classes"
    assert any(c["class_id"] == cls.id for c in out["items"])


def test_show_students_filter_by_barrier(db_session):
    _, _, cls = _make_org(db_session)
    _make_student(db_session, cls, name="小明", profile={"concept": 0.8, "reading": 0.1, "expression": 0.1})
    _make_student(db_session, cls, name="小红", profile={"concept": 0.1, "reading": 0.8, "expression": 0.1})
    out = run(tools_diagnosis.show_students(_teacher_ctx(db_session), class_id=cls.id, barrier_filter="reading"))
    assert out["mode"] == "students"
    assert len(out["items"]) == 1
    assert out["items"][0]["name"] == "小红"


# ---------------- weekly_report ----------------

def test_weekly_report_no_data_branch(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    out = run(tools_diagnosis.weekly_report(_teacher_ctx(db_session, llm=FakeLLM()), student_id=s.id))
    assert out["no_data"] is True
    assert out["report"]["summary"] == "本周暂无练习记录"


def test_weekly_report_generates(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    _make_exam_answer(db_session, s, cls)
    out = run(tools_diagnosis.weekly_report(_teacher_ctx(db_session, llm=FakeLLM()), student_id=s.id))
    assert out["no_data"] is False
    assert out["report"]["summary"] == "本周表现良好"


# ---------------- assign_adaptive_practice ----------------

def test_assign_adaptive_practice(monkeypatch, db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)

    class FakeAdaptive:
        def __init__(self, db):
            pass

        def generate_batch(self, student_ids, count=3, name="自适应练习", exclude_ref_ids=()):
            return {
                "results": [{"student_id": sid, "exam_id": 1, "question_count": count, "difficulty": "medium"} for sid in student_ids],
                "batch_limit": 5,
                "remaining": 0,
            }

    monkeypatch.setattr(tools_diagnosis, "AdaptivePracticeService", FakeAdaptive)
    out = run(tools_diagnosis.assign_adaptive_practice(_teacher_ctx(db_session), class_id=cls.id, count=3))
    assert out["class_id"] == cls.id
    assert out["results"][0]["student_id"] == s.id


# ---------------- generate_learning_plan / send_learning_plan ----------------

def test_generate_learning_plan_route(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    out = run(tools_diagnosis.generate_learning_plan(_teacher_ctx(db_session), student_id=s.id))
    assert out["_route"]["page"] == "students"
    assert out["_route"]["params"]["student_id"] == s.id
    assert out["_route"]["params"]["action"] == "learning-plan"


def test_send_learning_plan_persists_and_notifies(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    parent = Parent(name="明爸", phone="13800000001")
    db_session.add(parent)
    db_session.flush()
    db_session.add(StudentParentBinding(parent_id=parent.id, student_id=s.id, bind_code="123456", status="active"))
    db_session.flush()
    out = run(tools_diagnosis.send_learning_plan(
        _teacher_ctx(db_session), student_id=s.id,
        plan_data={"title": "氧化还原专项", "summary": "两周计划"},
    ))
    assert out["sent"] is True
    assert out["notified_parents"] == 1
    db_session.refresh(s)
    assert s.learning_plan["title"] == "氧化还原专项"
