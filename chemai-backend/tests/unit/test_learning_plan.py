"""学习计划规则引擎生成器 + 落库/通知单测（tasks 1.1/1.2）。

覆盖：薄弱知识点按 mastery 升序排序、plan_text+plan_data.days 格式结构、
无作答数据兜底、apply_plan 落库 + 家长通知（active 才通知）。
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import NotFoundError
from app.core.security import hash_password
from app.db.models import (
    Account,
    AccountRole,
    AuditStatus,
    Class,
    Difficulty,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Parent,
    ParentNotification,
    Question,
    QuestionSource,
    School,
    Student,
    StudentAnswer,
    StudentParentBinding,
    Teacher,
    TeacherStatus,
)
from app.db.session import get_db
from app.main import app
from app.services.diagnosis.learning_plan import apply_plan, generate_plan


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


def _make_student(db, cls, name="小明", profile=None):
    s = Student(
        class_id=cls.id,
        name=name,
        barrier_profile=profile or {"concept": 0.7, "reading": 0.2, "expression": 0.1},
    )
    db.add(s)
    db.flush()
    return s


def _answer(db, cls, student, kp, is_correct, q_index):
    """一题作答：题目带单一知识点，correct 决定该知识点掌握度。"""
    exam = ExamRecord(
        class_id=cls.id, student_id=student.id, name="练习",
        exam_type=ExamType.practice, status=ExamStatus.published,
        exam_date=datetime.date.today(), attendee_count=0, stats={}, question_stats={},
    )
    db.add(exam)
    db.flush()
    q = Question(
        content=f"题目{q_index}", options=[], answer="A", analysis="x",
        knowledge_points=kp, difficulty=Difficulty.easy,
        source=QuestionSource.practice, audit_status=AuditStatus.passed, audit_report={},
        record_id=exam.id,
    )
    db.add(q)
    db.flush()
    db.add(StudentAnswer(
        exam_id=exam.id, student_id=student.id, question_id=q.id,
        answer_text="A", is_correct=is_correct, answered_at=datetime.datetime.now(),
    ))
    db.flush()


def test_generate_plan_no_data_returns_empty(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    out = generate_plan(db_session, s.id)
    assert out == {"empty": True, "message": "暂无足够学习数据"}


def test_generate_plan_weak_points_sorted_ascending(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    # 掌握度：氧化还原 0.0 < 离子共存 0.33 < 水 1.0（非薄弱）
    _answer(db_session, cls, s, "氧化还原", is_correct=False, q_index=1)
    _answer(db_session, cls, s, "离子共存", is_correct=False, q_index=2)
    _answer(db_session, cls, s, "离子共存", is_correct=True, q_index=3)
    _answer(db_session, cls, s, "水", is_correct=True, q_index=4)
    plan = generate_plan(db_session, s.id)
    weak_names = [day["tasks"] for day in plan["plan_data"]["days"]]
    # 第 1 天任务含最薄弱知识点，且按掌握度升序
    assert any("氧化还原" in task for task in weak_names[0])
    assert any("离子共存" in task for task in weak_names[1])
    assert all("水" not in task for tasks in weak_names for task in tasks)


def test_generate_plan_structure(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls, profile={"concept": 0.1, "reading": 0.8, "expression": 0.1})
    _answer(db_session, cls, s, "氧化还原", is_correct=False, q_index=1)
    plan = generate_plan(db_session, s.id)
    assert "empty" not in plan
    assert isinstance(plan["title"], str)
    assert plan["title"].endswith("天）")
    assert "## 学习计划" in plan["plan_text"]
    days = plan["plan_data"]["days"]
    assert isinstance(days, list) and days
    for day in days:
        assert "label" in day and isinstance(day["label"], str)
        assert "tasks" in day and isinstance(day["tasks"], list)
        assert all(isinstance(t, str) for t in day["tasks"])
    # 主导障碍 reading 应在第 1 天插入针对性任务
    assert any("审题" in t for t in days[0]["tasks"])


def test_generate_plan_max_three_weak(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    for i, kp in enumerate(["a", "b", "c", "d"]):
        _answer(db_session, cls, s, kp, is_correct=False, q_index=i + 1)
    plan = generate_plan(db_session, s.id)
    assert len(plan["plan_data"]["days"]) == 3


def test_generate_plan_all_correct_falls_back(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    for i, kp in enumerate(["a", "b", "c"]):
        _answer(db_session, cls, s, kp, is_correct=True, q_index=i + 1)
    plan = generate_plan(db_session, s.id)
    assert "empty" not in plan
    assert plan["plan_data"]["days"]


def test_apply_plan_persists_and_notifies_active_binding(db_session):
    _, _, cls = _make_org(db_session)
    s = _make_student(db_session, cls)
    p1 = Parent(name="明爸", phone="13800000001")
    p2 = Parent(name="明妈", phone="13800000002")
    db_session.add(p1)
    db_session.add(p2)
    db_session.flush()
    db_session.add(StudentParentBinding(parent_id=p1.id, student_id=s.id, bind_code="123456", status="active"))
    db_session.add(StudentParentBinding(parent_id=p2.id, student_id=s.id, bind_code="123456", status="inactive"))
    db_session.flush()
    plan = {"title": "氧化还原专项", "summary": "两周计划"}
    out = apply_plan(db_session, s.id, plan)
    assert out["sent"] is True
    assert out["notified_parents"] == 1
    assert out["name"] == "小明"
    db_session.refresh(s)
    assert s.learning_plan == plan


def test_apply_plan_unknown_student_raises(db_session):
    with pytest.raises(NotFoundError):
        apply_plan(db_session, 999, {"title": "x"})


def test_generate_plan_unknown_student_raises(db_session):
    with pytest.raises(NotFoundError):
        generate_plan(db_session, 999)


# ---------------- REST 端点（tasks 2.2） ----------------

@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _account(db_session, username, role, role_id):
    acc = Account(username=username, password_hash=hash_password("Passw0rd!"), role=role, role_id=role_id)
    db_session.add(acc)
    db_session.commit()
    return acc


def _teacher_token(client, db_session, school):
    teacher = Teacher(school_id=school.id, name="王老师", phone="13800000002", status=TeacherStatus.approved)
    db_session.add(teacher)
    db_session.flush()
    _account(db_session, "t_plan", AccountRole.teacher, teacher.id)
    login = client.post("/api/auth/login", json={"username": "t_plan", "password": "Passw0rd!"})
    return login.json()["access_token"], teacher


def _student_token(client, db_session, cls, name="张三"):
    student = Student(class_id=cls.id, name=name)
    db_session.add(student)
    db_session.flush()
    _account(db_session, name[:2] + "_plan", AccountRole.student, student.id)
    login = client.post("/api/auth/login", json={"username": name[:2] + "_plan", "password": "Passw0rd!"})
    return login.json()["access_token"], student


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _bind_parent(db_session, student):
    parent = Parent(name="明爸", phone="13800000003")
    db_session.add(parent)
    db_session.flush()
    db_session.add(StudentParentBinding(parent_id=parent.id, student_id=student.id, bind_code="123456", status="active"))
    db_session.commit()
    return parent


def test_endpoint_teacher_generate_preview_not_persisted(client, db_session):
    school, _, cls = _make_org(db_session)
    token, _ = _teacher_token(client, db_session, school)
    s = _make_student(db_session, cls)
    _answer(db_session, cls, s, "氧化还原", is_correct=False, q_index=1)
    db_session.commit()

    resp = client.post("/api/diagnosis/learning-plan/generate", headers=_auth(token), json={"student_id": s.id})
    assert resp.status_code == 200
    body = resp.json()
    assert "title" in body and "plan_text" in body and "days" in body["plan_data"]
    db_session.refresh(s)
    assert s.learning_plan == {}  # 预览不落库


def test_endpoint_generate_no_data_409(client, db_session):
    school, _, cls = _make_org(db_session)
    token, _ = _teacher_token(client, db_session, school)
    s = _make_student(db_session, cls)
    db_session.commit()
    resp = client.post("/api/diagnosis/learning-plan/generate", headers=_auth(token), json={"student_id": s.id})
    assert resp.status_code == 409


def test_endpoint_teacher_apply_persists_and_notifies(client, db_session):
    school, _, cls = _make_org(db_session)
    token, _ = _teacher_token(client, db_session, school)
    s = _make_student(db_session, cls)
    parent = _bind_parent(db_session, s)
    plan = {"title": "氧化还原专项", "plan_text": "## 学习计划", "plan_data": {"days": []}}

    resp = client.post(f"/api/diagnosis/learning-plan/apply/{s.id}", headers=_auth(token), json={"plan": plan})
    assert resp.status_code == 200
    assert resp.json()["notified_parents"] == 1
    db_session.refresh(s)
    assert s.learning_plan["title"] == "氧化还原专项"
    notes = db_session.query(ParentNotification).filter(ParentNotification.parent_id == parent.id).all()
    assert len(notes) == 1
    assert notes[0].notification_type.value == "learning_plan"


def test_endpoint_student_generate_403(client, db_session):
    _, _, cls = _make_org(db_session)
    token, s = _student_token(client, db_session, cls)
    resp = client.post("/api/diagnosis/learning-plan/generate", headers=_auth(token), json={"student_id": s.id})
    assert resp.status_code == 403


def test_endpoint_student_apply_403(client, db_session):
    _, _, cls = _make_org(db_session)
    token, s = _student_token(client, db_session, cls)
    resp = client.post(f"/api/diagnosis/learning-plan/apply/{s.id}", headers=_auth(token), json={"plan": {"title": "x"}})
    assert resp.status_code == 403


def test_endpoint_student_get_own_ok_others_403(client, db_session):
    _, _, cls = _make_org(db_session)
    token, s = _student_token(client, db_session, cls, name="张三")
    other = Student(class_id=cls.id, name="李四")
    db_session.add(other)
    db_session.commit()

    own = client.get(f"/api/diagnosis/learning-plan/{s.id}", headers=_auth(token))
    assert own.status_code == 200
    assert own.json()["learning_plan"] is None

    forbidden = client.get(f"/api/diagnosis/learning-plan/{other.id}", headers=_auth(token))
    assert forbidden.status_code == 403


def test_endpoint_teacher_get_any_student(client, db_session):
    school, _, cls = _make_org(db_session)
    token, _ = _teacher_token(client, db_session, school)
    s = _make_student(db_session, cls)
    db_session.commit()
    resp = client.get(f"/api/diagnosis/learning-plan/{s.id}", headers=_auth(token))
    assert resp.status_code == 200
