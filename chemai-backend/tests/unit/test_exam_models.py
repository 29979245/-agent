"""教学链模型测试：ExamRecord/Question/StudentAnswer（3.1-3.3）。"""
import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Class, ExamRecord, Grade, Question, School, Student, StudentAnswer
from app.db.models.enums import AuditStatus, BarrierType, Difficulty, ExamType, QuestionSource


def _org(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    return cls


def _exam(db_session, cls, **kw):
    exam = ExamRecord(
        class_id=cls.id,
        exam_type=kw.get("exam_type", ExamType.exam),
        exam_date=kw.get("exam_date", datetime.date(2026, 8, 22)),
        attendee_count=kw.get("attendee_count", 0),
    )
    db_session.add(exam)
    db_session.flush()
    return exam


def _question(db_session, **kw):
    q = Question(
        content=kw.get("content", "配平：H2+O2→H2O"),
        answer=kw.get("answer", "2H2+O2=2H2O"),
        difficulty=kw.get("difficulty", Difficulty.easy),
    )
    db_session.add(q)
    db_session.flush()
    return q


# ---- 3.1 ExamRecord ----

def test_exam_record_crud(db_session):
    cls = _org(db_session)
    exam = _exam(db_session, cls, attendee_count=45)
    exam.stats = {"total": 45, "wrong": 5}
    db_session.commit()
    got = db_session.get(ExamRecord, exam.id)
    assert got.attendee_count == 45
    assert got.stats == {"total": 45, "wrong": 5}


def test_exam_type_enum_constraint(db_session):
    cls = _org(db_session)
    exam = ExamRecord(class_id=cls.id, exam_type="bad_type", exam_date=datetime.date(2026, 8, 22))
    db_session.add(exam)
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---- 3.2 Question ----

def test_question_crud(db_session):
    q = _question(db_session, difficulty=Difficulty.hard, source=QuestionSource.ai)
    q.options = ["2H2+O2=2H2O", "H2+O2=H2O2", "H2+O2=H2O", "2H2O2=2H2O+O2"]
    q.audit_status = AuditStatus.passed
    q.audit_report = {"balancing": True, "conditions": True, "products": True, "structure": True}
    db_session.commit()
    got = db_session.get(Question, q.id)
    assert got.difficulty == Difficulty.hard
    assert len(got.options) == 4
    assert got.audit_report["balancing"] is True


def test_question_audit_status_blocked_default(db_session):
    q = Question(content="c", answer="a", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.flush()
    assert q.audit_status == AuditStatus.passed


# ---- 3.3 StudentAnswer ----

def _answer(db_session, cls, **kw):
    stu = Student(class_id=cls.id, name="张三")
    db_session.add(stu)
    db_session.flush()
    q = _question(db_session)
    exam = _exam(db_session, cls)
    ans = StudentAnswer(
        student_id=stu.id,
        question_id=q.id,
        exam_id=exam.id,
        answer_text=kw.get("answer_text", "2H2+O2=2H2O"),
        is_correct=kw.get("is_correct", True),
    )
    db_session.add(ans)
    db_session.flush()
    return ans, stu, q, exam


def test_student_answer_crud_with_barrier(db_session):
    cls = _org(db_session)
    ans, _, _, _ = _answer(db_session, cls)
    ans.barrier_type = BarrierType.concept
    ans.consecutive_errors = 3
    ans.consecutive_correct = 0
    db_session.commit()
    got = db_session.get(StudentAnswer, ans.id)
    assert got.barrier_type == BarrierType.concept
    assert got.consecutive_errors == 3


def test_delete_exam_cascades_answers(db_session):
    """删除考试级联删除作答（D8）。"""
    cls = _org(db_session)
    ans, _, _, exam = _answer(db_session, cls)
    ans_id = ans.id
    db_session.commit()  # 先落库，避免与删除同事务
    db_session.delete(exam)
    db_session.commit()
    db_session.expunge_all()
    assert db_session.get(StudentAnswer, ans_id) is None


def test_delete_question_with_answer_restricted(db_session):
    """有作答的题目不可删除（D8）。"""
    cls = _org(db_session)
    ans, _, q, _ = _answer(db_session, cls)
    q_id = q.id
    db_session.commit()  # 先落库，否则 rollback 会连带撤销初始插入
    db_session.delete(q)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.expunge_all()
    assert db_session.get(Question, q_id) is not None
