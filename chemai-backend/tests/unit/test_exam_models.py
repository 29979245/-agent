"""教学链模型测试：ExamRecord/Question/StudentAnswer（3.1-3.3）。"""
import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Class,
    ExamRecord,
    Grade,
    Question,
    QuestionSet,
    QuestionSetItem,
    School,
    Student,
    StudentAnswer,
)
from app.db.models.enums import (
    AuditStatus,
    BarrierType,
    Difficulty,
    ExamStatus,
    ExamType,
    QuestionSource,
)


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


# ---- 1.4 ExamRecord 扩展字段 ----

def test_exam_record_new_fields(db_session):
    """ExamRecord 新字段：name/status/question_stats 可读写，默认 draft。"""
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    assert exam.name == ""
    assert exam.status == ExamStatus.draft
    assert exam.question_stats == {}
    exam.name = "期中化学测试"
    exam.status = ExamStatus.completed
    exam.question_stats = {"published": True, "question_count": 12, "total_students": 40}
    db_session.commit()
    got = db_session.get(ExamRecord, exam.id)
    assert got.name == "期中化学测试"
    assert got.status == ExamStatus.completed
    assert got.question_stats["question_count"] == 12


def test_exam_record_status_enum_constraint(db_session):
    """非法 status 写入被数据库拒绝。"""
    cls = _org(db_session)
    exam = ExamRecord(class_id=cls.id, exam_type=ExamType.exam, exam_date=datetime.date(2026, 8, 22))
    exam.status = "bad_status"
    db_session.add(exam)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_question_record_id(db_session):
    """Question.record_id 关联考试，置空不报错。"""
    cls = _org(db_session)
    exam = _exam(db_session, cls)
    q = _question(db_session)
    q.record_id = exam.id
    db_session.commit()
    got = db_session.get(Question, q.id)
    assert got.record_id == exam.id
    assert got.exam is not None


# ---- 1.2 / 1.3 QuestionSet / QuestionSetItem ----

def test_question_set_crud(db_session):
    """题库文件夹 CRUD，teacher_id/region/year 齐全。"""
    qs = QuestionSet(name="2024 高考真题", teacher_id=1, region="全国卷", year=2024, question_count=0)
    db_session.add(qs)
    db_session.commit()
    got = db_session.get(QuestionSet, qs.id)
    assert got.name == "2024 高考真题"
    assert got.region == "全国卷"
    assert got.year == 2024
    assert got.is_preset is False


def test_question_set_item_association_and_cascade(db_session):
    """QuestionSetItem 关联查询可用；删除题库仅删关联、题目实体保留。"""
    qs = QuestionSet(name="基础训练")
    db_session.add(qs)
    db_session.flush()
    q1 = _question(db_session, content="题A")
    q2 = _question(db_session, content="题B")
    db_session.add_all([
        QuestionSetItem(set_id=qs.id, question_id=q1.id, sort_order=0),
        QuestionSetItem(set_id=qs.id, question_id=q2.id, sort_order=1),
    ])
    db_session.commit()
    qs_id = qs.id
    q1_id, q2_id = q1.id, q2.id
    db_session.expunge_all()
    got = db_session.get(QuestionSet, qs_id)
    assert len(got.items) == 2
    db_session.delete(got)
    db_session.commit()
    db_session.expunge_all()
    # 关联条目级联删除，题目实体保留（D8）
    assert db_session.query(QuestionSetItem).filter_by(set_id=qs_id).count() == 0
    assert db_session.get(Question, q1_id) is not None
    assert db_session.get(Question, q2_id) is not None
