"""绑定码生成端点测试（change student-supplement-apis task 4.1）。

覆盖：本人 200 且格式合规（长度 6、大写字母数字、无易混淆字符）、非本人 403、
重新生成覆盖旧值、未认证 401。
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db.models import Account, Class, Grade, School, Student
from app.db.models.enums import AccountRole
from app.db.session import get_db
from app.main import app

_ALLOWED = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


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
    return cls


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


def _auth(account_id):
    return {"Authorization": f"Bearer {create_token(user_id=account_id, role='student')}"}


def test_generate_bind_code_200_and_format(client, db_session):
    cls = _org(db_session)
    stu = _student(db_session, cls)
    account = _student_account(db_session, stu)
    resp = client.post(f"/api/student/{stu.id}/bind-code", headers=_auth(account.id))
    assert resp.status_code == 200
    code = resp.json()["bind_code"]
    assert len(code) == 6
    assert all(ch in _ALLOWED for ch in code)  # 大写字母数字且无 0/O/1/I
    db_session.refresh(stu)
    assert stu.bind_code == code


def test_generate_bind_code_other_student_403(client, db_session):
    cls = _org(db_session)
    stu_a = _student(db_session, cls, name="甲")
    stu_b = _student(db_session, cls, name="乙")
    account_a = _student_account(db_session, stu_a)
    resp = client.post(f"/api/student/{stu_b.id}/bind-code", headers=_auth(account_a.id))
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PERMISSION_DENIED"


def test_regenerate_overwrites_old_code(client, db_session):
    cls = _org(db_session)
    stu = _student(db_session, cls)
    account = _student_account(db_session, stu)
    first = client.post(f"/api/student/{stu.id}/bind-code", headers=_auth(account.id)).json()["bind_code"]
    second = client.post(f"/api/student/{stu.id}/bind-code", headers=_auth(account.id)).json()["bind_code"]
    assert second != first  # 重新生成覆盖旧值
    db_session.refresh(stu)
    assert stu.bind_code == second


def test_generate_bind_code_unauthorized_401(client, db_session):
    cls = _org(db_session)
    stu = _student(db_session, cls)
    resp = client.post(f"/api/student/{stu.id}/bind-code")
    assert resp.status_code == 401


def test_generate_bind_code_admin_missing_student_404(client, db_session):
    cls = _org(db_session)
    _student(db_session, cls)
    # admin 有 report:update 且不做 self-check，命中学生不存在的 404 分支
    admin = Account(username="admin1", password_hash="x", role=AccountRole.admin, role_id=1)
    db_session.add(admin)
    db_session.flush()
    db_session.commit()
    resp = client.post(
        "/api/student/999/bind-code",
        headers={"Authorization": f"Bearer {create_token(user_id=admin.id, role='admin')}"},
    )
    assert resp.status_code == 404


def test_generate_bind_code_student_other_missing_403(client, db_session):
    cls = _org(db_session)
    stu = _student(db_session, cls)
    account = _student_account(db_session, stu)
    # 学生请求任意非本人（含不存在）绑定码 → self-check 403，不泄露存在性
    resp = client.post("/api/student/999/bind-code", headers=_auth(account.id))
    assert resp.status_code == 403
