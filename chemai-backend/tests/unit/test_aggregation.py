"""画像聚合测试（doc 48 §6 + 评审 T1 冻结语义 / T11 防御性归一化）。

覆盖：聚合纯函数（空/补零/保留 2 位/忽略无效）、barrier_profile 防御性归一化
（NULL/缺键/畸形）、DB 落库更新、冻结学生跳过聚合、解除冻结恢复聚合。
"""
from datetime import datetime

from sqlalchemy import text

from app.db.models import (
    Class,
    ExamRecord,
    Grade,
    Question,
    School,
    Student,
    StudentAnswer,
)
from app.db.models.enums import Difficulty, ExamType
from app.services.diagnosis.aggregation import aggregate, normalize_profile, refresh_profiles


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
    return cls


def _student(db, cls, name="张三", code="123456"):
    s = Student(class_id=cls.id, name=name, bind_code=code)
    db.add(s)
    db.flush()
    return s


def _exam(db, cls):
    e = ExamRecord(class_id=cls.id, name="期中考试", exam_type=ExamType.exam,
                   exam_date=datetime.utcnow().date())
    db.add(e)
    db.flush()
    return e


def _question(db, content="在密闭容器中加热高锰酸钾"):
    q = Question(content=content, answer="B", difficulty=Difficulty.medium)
    db.add(q)
    db.flush()
    return q


def _answer(db, student, question, exam, barrier):
    a = StudentAnswer(
        student_id=student.id, question_id=question.id, exam_id=exam.id,
        answer_text="C", is_correct=False, barrier_type=barrier,
    )
    db.add(a)
    db.flush()
    return a


# ---------------- 5.1 聚合纯函数 ----------------

def test_aggregate_empty_no_div_zero():
    assert aggregate([]) == {"concept": 0.0, "reading": 0.0, "expression": 0.0}


def test_aggregate_only_concept_pads_others():
    assert aggregate(["concept", "concept"]) == {
        "concept": 1.0, "reading": 0.0, "expression": 0.0,
    }


def test_aggregate_rounds_to_2dp():
    result = aggregate(["concept", "concept", "reading"])
    assert result["concept"] == round(2 / 3, 2)
    assert result["reading"] == round(1 / 3, 2)
    assert result["expression"] == 0.0


def test_aggregate_ignores_none_and_invalid():
    result = aggregate(["concept", None, "typo", "expression"])
    assert result == {"concept": 0.5, "reading": 0.0, "expression": 0.5}


# ---------------- 1.2a 防御性归一化 ----------------

def test_normalize_profile_none():
    assert normalize_profile(None) == {"concept": 0.0, "reading": 0.0, "expression": 0.0}


def test_normalize_profile_missing_key():
    assert normalize_profile({"concept": 0.8}) == {
        "concept": 0.8, "reading": 0.0, "expression": 0.0,
    }


def test_normalize_profile_malformed():
    assert normalize_profile("不是JSON") == {"concept": 0.0, "reading": 0.0, "expression": 0.0}
    assert normalize_profile([1, 2]) == {"concept": 0.0, "reading": 0.0, "expression": 0.0}


def test_normalize_profile_valid():
    assert normalize_profile({"concept": 0.6, "reading": 0.2, "expression": 0.2}) == {
        "concept": 0.6, "reading": 0.2, "expression": 0.2,
    }


def test_normalize_profile_nan_and_inf():
    # code review 加固：NaN/inf 会静默污染 class_stats 平均值 → 一律归零（T11 防御扩展）
    assert normalize_profile({"concept": float("nan"), "reading": 0.5, "expression": 0.0}) == {
        "concept": 0.0, "reading": 0.5, "expression": 0.0,
    }
    assert normalize_profile(
        {"concept": float("inf"), "reading": -float("inf"), "expression": 0.0}
    ) == {"concept": 0.0, "reading": 0.0, "expression": 0.0}


# ---------------- 5.2 / 5.2a 落库更新 + 冻结语义 ----------------

def test_db_refresh_profiles_updates(db_session):
    cls = _org(db_session)
    s1 = _student(db_session, cls, name="张三", code="100001")
    s2 = _student(db_session, cls, name="李四", code="100002")
    exam = _exam(db_session, cls)
    q1, q2, q3, q4 = (_question(db_session) for _ in range(4))
    _answer(db_session, s1, q1, exam, "concept")
    _answer(db_session, s1, q2, exam, "concept")
    _answer(db_session, s1, q3, exam, "reading")
    _answer(db_session, s2, q4, exam, "expression")

    refresh_profiles(db_session)

    db_session.refresh(s1)
    db_session.refresh(s2)
    assert s1.barrier_profile == {
        "concept": round(2 / 3, 2), "reading": round(1 / 3, 2), "expression": 0.0,
    }
    assert s1.barrier_last_updated is not None
    assert s2.barrier_profile == {"concept": 0.0, "reading": 0.0, "expression": 1.0}
    assert s2.barrier_last_updated is not None


def test_db_frozen_student_skipped(db_session):
    cls = _org(db_session)
    frozen = _student(db_session, cls, name="冻结生", code="100003")
    frozen.barrier_frozen = True  # 教师 override 后冻结
    frozen.barrier_profile = {"concept": 1.0, "reading": 0.0, "expression": 0.0}
    db_session.commit()
    exam = _exam(db_session, cls)
    q = _question(db_session)
    _answer(db_session, frozen, q, exam, "reading")

    refresh_profiles(db_session)

    db_session.refresh(frozen)
    assert frozen.barrier_profile == {"concept": 1.0, "reading": 0.0, "expression": 0.0}
    assert frozen.barrier_last_updated is None


def test_db_unfreeze_resumes_aggregation(db_session):
    cls = _org(db_session)
    s = _student(db_session, cls, name="恢复生", code="100004")
    s.barrier_frozen = True
    db_session.commit()
    exam = _exam(db_session, cls)
    q = _question(db_session)
    _answer(db_session, s, q, exam, "reading")

    refresh_profiles(db_session)
    db_session.refresh(s)
    assert s.barrier_profile == {}  # 冻结期间不聚合

    s.barrier_frozen = False
    db_session.commit()
    refresh_profiles(db_session)
    db_session.refresh(s)
    assert s.barrier_profile == {"concept": 0.0, "reading": 1.0, "expression": 0.0}
    assert s.barrier_last_updated is not None


# ---------------- 9.1 覆盖率补齐（student_ids 过滤 / 孤儿答案跳过） ----------------

def test_db_refresh_profiles_filters_student_ids(db_session):
    cls = _org(db_session)
    s1 = _student(db_session, cls, name="张三", code="200001")
    s2 = _student(db_session, cls, name="李四", code="200002")
    exam = _exam(db_session, cls)
    q1, q2 = _question(db_session), _question(db_session)
    _answer(db_session, s1, q1, exam, "concept")
    _answer(db_session, s2, q2, exam, "expression")

    updated = refresh_profiles(db_session, student_ids=[s1.id])

    db_session.refresh(s1)
    db_session.refresh(s2)
    assert updated == 1
    assert s1.barrier_profile == {"concept": 1.0, "reading": 0.0, "expression": 0.0}
    assert s2.barrier_profile == {}  # 不在 student_ids 内 → 不聚合
    assert s2.barrier_last_updated is None


def test_db_refresh_skips_orphan_answer_student(db_session):
    cls = _org(db_session)
    s = _student(db_session, cls, name="孤儿生", code="200003")
    exam = _exam(db_session, cls)
    q = _question(db_session)
    _answer(db_session, s, q, exam, "reading")
    db_session.commit()

    # 制造孤儿答案：临时关闭 FK 后删除学生行，保留作答记录
    db_session.execute(text("PRAGMA foreign_keys=OFF"))
    db_session.execute(text("DELETE FROM student WHERE id = :sid"), {"sid": s.id})
    db_session.commit()
    db_session.execute(text("PRAGMA foreign_keys=ON"))
    db_session.commit()
    db_session.expunge_all()  # 清 identity map，session.get 走 DB 返回 None

    updated = refresh_profiles(db_session)
    assert updated == 0  # 作答记录的学生已删除 → 跳过
