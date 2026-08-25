"""诊断链数据模型测试（doc 48 §8 + 评审 T2）。

覆盖：BarrierConfig 默认阈值与唯一 teacher_id、BarrierOverrideLog 留痕、
Student.barrier_last_updated/barrier_frozen、StudentAnswer 平铺诊断列。
"""
from datetime import datetime

from app.db.models import (
    BarrierConfig,
    BarrierOverrideLog,
    Class,
    Grade,
    Question,
    School,
    Student,
    StudentAnswer,
    Teacher,
)
from app.db.models.enums import Difficulty, TeacherStatus


def _org(db):
    school = School(name="测试学校", current_semester="2026-1")
    db.add(school)
    db.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(grade)
    db.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db.add(cls)
    db.flush()
    return school, grade, cls


def _teacher(db, school):
    t = Teacher(school_id=school.id, name="王老师", phone="13800000002", status=TeacherStatus.approved)
    db.add(t)
    db.flush()
    return t


def _student(db, cls):
    s = Student(class_id=cls.id, name="张三", bind_code="123456")
    db.add(s)
    db.flush()
    return s


def test_barrier_config_defaults(db_session):
    school = _org(db_session)[0]
    teacher = _teacher(db_session, school)
    cfg = BarrierConfig(teacher_id=teacher.id)
    db_session.add(cfg)
    db_session.commit()
    assert cfg.consecutive_error_threshold == 3
    assert cfg.consecutive_correct_threshold == 2
    assert cfg.low_score_threshold == 3
    assert cfg.warning_threshold == 3
    assert cfg.enabled is False


def test_barrier_config_teacher_unique(db_session):
    school = _org(db_session)[0]
    teacher = _teacher(db_session, school)
    db_session.add(BarrierConfig(teacher_id=teacher.id))
    db_session.commit()
    db_session.add(BarrierConfig(teacher_id=teacher.id))
    import pytest

    with pytest.raises(Exception):
        db_session.commit()
    db_session.rollback()


def test_barrier_override_log_retention(db_session):
    school, _, cls = _org(db_session)
    teacher = _teacher(db_session, school)
    student = _student(db_session, cls)
    log = BarrierOverrideLog(
        teacher_id=teacher.id,
        student_id=student.id,
        before={"concept": 0.6, "reading": 0.2, "expression": 0.2},
        after={"concept": 0.9, "reading": 0.05, "expression": 0.05},
        reason="教师复核确认概念障碍",
    )
    db_session.add(log)
    db_session.commit()
    assert log.id is not None
    assert log.after["concept"] == 0.9
    assert isinstance(log.created_at, datetime)
    assert log.created_at is not None


def test_student_barrier_last_updated_and_frozen(db_session):
    _, _, cls = _org(db_session)
    s = _student(db_session, cls)
    assert s.barrier_last_updated is None
    assert s.barrier_frozen is False
    s.barrier_last_updated = datetime.utcnow()
    s.barrier_frozen = True
    db_session.commit()
    assert s.barrier_frozen is True
    assert s.barrier_last_updated is not None


def test_student_answer_flat_diagnosis_columns(db_session):
    _, _, cls = _org(db_session)
    student = _student(db_session, cls)
    question = Question(
        content="在密闭容器中加热高锰酸钾，观察到的现象是？",
        answer="B",
        difficulty=Difficulty.medium,
    )
    db_session.add(question)
    db_session.flush()
    from app.db.models import ExamRecord
    from app.db.models.enums import ExamType

    exam = ExamRecord(
        class_id=cls.id,
        name="期中考试",
        exam_type=ExamType.exam,
        exam_date=datetime.utcnow().date(),
    )
    db_session.add(exam)
    db_session.flush()
    ans = StudentAnswer(
        student_id=student.id,
        question_id=question.id,
        exam_id=exam.id,
        answer_text="C",
        is_correct=False,
        fused_conf=0.54,
        rule_conf=0.9,
        llm_conf=None,
        diagnosis_flag="manual_review",
        diagnosis_version="1.0",
        diagnosis_source="rule",
        diagnosis_detail={"reasoning": "勒夏特列方向混淆", "suggestion": "回顾平衡移动方向"},
    )
    db_session.add(ans)
    db_session.commit()
    assert ans.barrier_type is None  # 平铺列不干扰既有主判
    assert ans.fused_conf == 0.54
    assert ans.diagnosis_flag == "manual_review"
    assert ans.diagnosis_detail["reasoning"] == "勒夏特列方向混淆"
    assert ans.diagnosis_version == "1.0"
