"""报告聚合端点测试（change student-supplement-apis task 3.1）。

覆盖：本人 200、非本人 403、教师读学生 200、未认证 401。
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token, hash_password
from app.db.models import Account, Class, Grade, School, Student, Teacher
from app.db.models.enums import AccountRole, TeacherStatus
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    def _teardown():
        app.dependency_overrides.pop(get_db, None)

    with TestClient(app) as c:
        yield c
    _teardown()


def _org(db_session):
    school = School(name="测试学校", current_semester="2026-1")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    return school, cls


def _student(db_session, cls, name="张三"):
    stu = Student(class_id=cls.id, name=name)
    db_session.add(stu)
    db_session.flush()
    return stu


def _student_account(db_session, stu):
    account = Account(
        username=f"s{stu.id}",
        password_hash="x",
        role=AccountRole.student,
        role_id=stu.id,
    )
    db_session.add(account)
    db_session.flush()
    db_session.commit()
    return account


def _teacher_account(db_session, school):
    teacher = Teacher(school_id=school.id, name="王老师", phone="13800000001", status=TeacherStatus.approved)
    db_session.add(teacher)
    db_session.flush()
    account = Account(
        username="t1",
        password_hash="x",
        role=AccountRole.teacher,
        role_id=teacher.id,
    )
    db_session.add(account)
    db_session.flush()
    db_session.commit()
    return account


def _auth(account_id, role="student"):
    return {"Authorization": f"Bearer {create_token(user_id=account_id, role=role)}"}


def test_own_report_200(client, db_session):
    _, cls = _org(db_session)
    stu = _student(db_session, cls)
    account = _student_account(db_session, stu)
    resp = client.get(f"/api/report/student/{stu.id}", headers=_auth(account.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["name"] == "张三"
    assert body["profile"]["class_name"] == "1班"
    assert body["stats"] == {
        "completed_exercises": 0,
        "accuracy": 0.0,
        "streak_days": 0,
    }
    assert body["learning_plan"] is None


def test_other_student_report_403(client, db_session):
    _, cls = _org(db_session)
    stu_a = _student(db_session, cls, name="甲")
    stu_b = _student(db_session, cls, name="乙")
    account_a = _student_account(db_session, stu_a)
    resp = client.get(f"/api/report/student/{stu_b.id}", headers=_auth(account_a.id))
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PERMISSION_DENIED"


def test_teacher_reads_student_report_200(client, db_session):
    school, cls = _org(db_session)
    stu = _student(db_session, cls)
    teacher = _teacher_account(db_session, school)
    resp = client.get(f"/api/report/student/{stu.id}", headers=_auth(teacher.id, role="teacher"))
    assert resp.status_code == 200
    assert resp.json()["profile"]["student_id"] == stu.id


def test_report_unauthorized_401(client, db_session):
    _, cls = _org(db_session)
    stu = _student(db_session, cls)
    resp = client.get(f"/api/report/student/{stu.id}")
    assert resp.status_code == 401


def test_report_missing_student_404(client, db_session):
    _, cls = _org(db_session)
    _student(db_session, cls)
    teacher = _teacher_account(db_session, cls.grade.school)
    resp = client.get("/api/report/student/999", headers=_auth(teacher.id, role="teacher"))
    assert resp.status_code == 404


def test_my_page_end_to_end_flow(client, db_session):
    """doc 52 Step A4 手工走查：登录 → 生成绑定码 → 报告页 bind_code 更新 → 改密后旧密失败新密成功。"""
    _, cls = _org(db_session)
    stu = _student(db_session, cls)
    account = Account(
        username="s1",
        password_hash=hash_password("Passw0rd!"),
        role=AccountRole.student,
        role_id=stu.id,
    )
    db_session.add(account)
    db_session.flush()
    db_session.commit()

    token = client.post("/api/auth/login", json={"username": "s1", "password": "Passw0rd!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    code = client.post(f"/api/student/{stu.id}/bind-code", headers=headers).json()["bind_code"]
    report = client.get(f"/api/report/student/{stu.id}", headers=headers).json()
    assert report["profile"]["bind_code"] == code  # 报告页展示最新绑定码

    resp = client.post(
        "/api/auth/change-password",
        json={"old_password": "Passw0rd!", "new_password": "NewPass6!"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert client.post("/api/auth/login", json={"username": "s1", "password": "Passw0rd!"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "s1", "password": "NewPass6!"}).status_code == 200
