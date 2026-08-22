"""组织链模型测试：School/Grade/Class/Teacher/Student/Account（2.1-2.6）。"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Account, Class, Grade, School, Student, Teacher
from app.db.models.account import validate_account_role_target
from app.db.models.enums import AccountRole, TeacherStatus, TeacherSubRole


def _make_school(session, **kw):
    school = School(name=kw.get("name", "示例中学"), region="长沙")
    session.add(school)
    session.flush()
    return school


def _make_grade(session, school, **kw):
    grade = Grade(school_id=school.id, name=kw.get("name", "高一"), academic_year="2026-2027")
    session.add(grade)
    session.flush()
    return grade


def _make_class(session, grade, **kw):
    cls = Class(grade_id=grade.id, name=kw.get("name", "高一(1)班"))
    session.add(cls)
    session.flush()
    return cls


def _make_student(session, cls, **kw):
    stu = Student(class_id=cls.id, name=kw.get("name", "张三"))
    session.add(stu)
    session.flush()
    return stu


# ---- 2.1 School ----

def test_school_crud(db_session):
    school = _make_school(db_session, name="长郡中学", region="长沙")
    db_session.commit()
    got = db_session.get(School, school.id)
    assert got.name == "长郡中学"
    assert got.region == "长沙"


def test_school_name_required(db_session):
    with pytest.raises(IntegrityError):
        school = School(region="长沙")
        db_session.add(school)
        db_session.flush()


# ---- 2.2 Grade ----

def test_grade_belongs_to_school(db_session):
    school = _make_school(db_session)
    grade = _make_grade(db_session, school)
    db_session.commit()
    assert grade.school_id == school.id
    assert school.grades == [grade]


def test_grade_requires_school(db_session):
    with pytest.raises(IntegrityError):
        db_session.add(Grade(name="高二", academic_year="2026-2027"))
        db_session.flush()


# ---- 2.3 Class ----

def test_class_belongs_to_grade(db_session):
    school = _make_school(db_session)
    grade = _make_grade(db_session, school)
    cls = _make_class(db_session, grade)
    db_session.commit()
    assert cls.grade_id == grade.id
    assert grade.classes == [cls]


# ---- 2.4 Teacher ----

def test_teacher_default_status_pending(db_session):
    school = _make_school(db_session)
    teacher = Teacher(school_id=school.id, name="李老师", phone="13800000001")
    db_session.add(teacher)
    db_session.flush()
    assert teacher.status == TeacherStatus.pending
    assert teacher.sub_role == TeacherSubRole.teacher


def test_teacher_phone_unique(db_session):
    school = _make_school(db_session)
    db_session.add(Teacher(school_id=school.id, name="A", phone="13800000002"))
    db_session.flush()
    with pytest.raises(IntegrityError):
        db_session.add(Teacher(school_id=school.id, name="B", phone="13800000002"))
        db_session.flush()


# ---- 2.5 Student ----

def test_student_json_round_trip(db_session):
    cls = _make_class(db_session, _make_grade(db_session, _make_school(db_session)))
    stu = _make_student(db_session, cls)
    stu.barrier_profile = {"concept": 0.30, "reading": 0.50, "expression": 0.20}
    stu.learning_plan = {"date": "2026-08-22", "items": ["复习离子方程式", "完成 3 道配平"]}
    db_session.commit()

    got = db_session.get(Student, stu.id)
    assert got.barrier_profile == {"concept": 0.30, "reading": 0.50, "expression": 0.20}
    assert got.learning_plan["items"] == ["复习离子方程式", "完成 3 道配平"]


def test_student_mutable_dict_persist_inplace(db_session):
    """MutableDict 原地修改后必须持久化（T4），避免静默丢写。"""
    cls = _make_class(db_session, _make_grade(db_session, _make_school(db_session)))
    stu = _make_student(db_session, cls)
    stu.barrier_profile = {"concept": 0.3, "reading": 0.5, "expression": 0.2}
    db_session.commit()

    got = db_session.get(Student, stu.id)
    got.barrier_profile["concept"] = 0.6  # 原地修改
    got.barrier_profile["reading"] = 0.2
    got.barrier_profile["expression"] = 0.2
    db_session.commit()

    again = db_session.get(Student, stu.id)
    assert again.barrier_profile == {"concept": 0.6, "reading": 0.2, "expression": 0.2}


def test_student_bind_code_default(db_session):
    cls = _make_class(db_session, _make_grade(db_session, _make_school(db_session)))
    stu = _make_student(db_session, cls)
    assert stu.bind_code == ""


# ---- 2.6 Account ----

def test_account_username_unique(db_session):
    school = _make_school(db_session)
    teacher = Teacher(school_id=school.id, name="李", phone="13800000003")
    db_session.add(teacher)
    db_session.flush()
    db_session.add(Account(username="lily", password_hash="h", role=AccountRole.teacher, role_id=teacher.id))
    db_session.flush()
    with pytest.raises(IntegrityError):
        db_session.add(Account(username="lily", password_hash="h2", role=AccountRole.teacher, role_id=teacher.id))
        db_session.flush()


def test_account_role_target_invariant(db_session):
    """T2：role↔role_id 指向类型一致。"""
    school = _make_school(db_session)
    cls = _make_class(db_session, _make_grade(db_session, school))
    teacher = Teacher(school_id=school.id, name="李", phone="13800000004")
    student = _make_student(db_session, cls, name="王五")
    db_session.add(teacher)
    db_session.flush()

    ok = Account(username="t1", password_hash="h", role=AccountRole.teacher, role_id=teacher.id)
    ok2 = Account(username="s1", password_hash="h", role=AccountRole.student, role_id=student.id)
    db_session.add_all([ok, ok2])
    db_session.commit()

    assert validate_account_role_target(db_session, db_session.get(Account, ok.id)) is True
    assert validate_account_role_target(db_session, db_session.get(Account, ok2.id)) is True


def test_account_role_target_invariant_wrong_type(db_session):
    """role=student 但 role_id 指向 teacher → 不变量失败。"""
    school = _make_school(db_session)
    teacher = Teacher(school_id=school.id, name="李", phone="13800000005")
    db_session.add(teacher)
    db_session.flush()
    bad = Account(username="x", password_hash="h", role=AccountRole.student, role_id=teacher.id)
    db_session.add(bad)
    db_session.commit()
    assert validate_account_role_target(db_session, db_session.get(Account, bad.id)) is False
