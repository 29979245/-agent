"""家长门户 API 测试（change parent-backend §1 认证+绑定）。

覆盖 1.1 require_parent / 1.2 require_bound_child / 1.3 bind / 1.4 解绑 /
1.5 children / 1.6 所有权守卫。
"""
import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.parent_auth import require_bound_child, require_parent
from app.core.security import create_token, hash_password
from app.db.models import (
    Account,
    Class,
    Grade,
    Parent,
    School,
    Student,
    StudentParentBinding,
    Teacher,
)
from app.db.models.enums import (
    AccountRole,
    ParentBindingRelation,
    ParentBindingStatus,
    TeacherStatus,
)
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
    return school, grade, cls


def _parent_account(db_session, phone="13900000001", name="张父"):
    parent = Parent(name=name, phone=phone)
    db_session.add(parent)
    db_session.flush()
    account = Account(
        username=f"parent_{parent.id}",
        password_hash=hash_password("x"),
        role=AccountRole.parent,
        role_id=parent.id,
    )
    db_session.add(account)
    db_session.flush()
    db_session.commit()
    return parent, account


def _student(db_session, cls_id, name="张三", bind_code="ABCDEF"):
    student = Student(class_id=cls_id, name=name, bind_code=bind_code)
    db_session.add(student)
    db_session.flush()
    return student


def _token(account) -> str:
    return create_token(account.id, AccountRole.parent.value)


def _teacher_token(db_session) -> str:
    school, _, cls = _org(db_session)
    teacher = Teacher(school_id=school.id, name="王老师", phone="13800000001", status=TeacherStatus.approved)
    db_session.add(teacher)
    db_session.flush()
    account = Account(
        username="t_parent_test",
        password_hash=hash_password("Passw0rd!"),
        role=AccountRole.teacher,
        role_id=teacher.id,
    )
    db_session.add(account)
    db_session.flush()
    db_session.commit()
    return create_token(account.id, AccountRole.teacher.value)


def _bind(db_session, parent, student, status=ParentBindingStatus.active, code="ABCDEF"):
    binding = StudentParentBinding(
        parent_id=parent.id,
        student_id=student.id,
        bind_code=code,
        relation=ParentBindingRelation.father,
        status=status,
    )
    db_session.add(binding)
    db_session.flush()
    db_session.commit()
    return binding


# ---- 1.1 require_parent ----

def test_require_parent_accepts_parent(client, db_session):
    parent, account = _parent_account(db_session)
    resp = client.get("/api/parent/children", headers={"Authorization": f"Bearer {_token(account)}"})
    assert resp.status_code == 200
    assert resp.json()["children"] == []


def test_require_parent_rejects_non_parent(client, db_session):
    token = _teacher_token(db_session)
    resp = client.get("/api/parent/children", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "PARENT_ONLY"


def test_require_parent_missing_token(client):
    resp = client.get("/api/parent/children")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTHENTICATION_REQUIRED"


# ---- 1.2 require_bound_child（依赖函数直测） ----

def test_bound_child_accepts_active(db_session):
    _, _, cls = _org(db_session)
    parent, _ = _parent_account(db_session)
    student = _student(db_session, cls.id)
    _bind(db_session, parent, student)
    # parent_id 由 require_parent 子依赖注入；此处直测绑定校验分支
    require_bound_child(student_id=student.id, parent_id=parent.id, db=db_session)


def test_bound_child_rejects_unbound(db_session):
    _, _, cls = _org(db_session)
    parent, _ = _parent_account(db_session)
    other_parent, _ = _parent_account(db_session, phone="13900000002")
    student = _student(db_session, cls.id)
    _bind(db_session, parent, student)
    with pytest.raises(ForbiddenError) as exc:
        require_bound_child(student_id=student.id, parent_id=other_parent.id, db=db_session)
    assert "未绑定该学生" in exc.value.detail["detail"]


def test_bound_child_rejects_missing_student(db_session):
    parent, _ = _parent_account(db_session)
    with pytest.raises(NotFoundError) as exc:
        require_bound_child(student_id=999999, parent_id=parent.id, db=db_session)
    assert exc.value.detail["error_code"] == "STUDENT_NOT_FOUND"


# ---- 1.3 bind ----

def test_bind_success_consumes_code(client, db_session):
    _, _, cls = _org(db_session)
    parent, account = _parent_account(db_session)
    student = _student(db_session, cls.id, bind_code="ABCDEF")
    db_session.commit()
    resp = client.post(
        "/api/parent/bind",
        json={"student_id": student.id, "bind_code": "ABCDEF", "relation": "father"},
        headers={"Authorization": f"Bearer {_token(account)}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    db_session.refresh(student)
    assert student.bind_code == ""  # 一次性码被消费


def test_bind_code_mismatch(client, db_session):
    _, _, cls = _org(db_session)
    parent, account = _parent_account(db_session)
    student = _student(db_session, cls.id, bind_code="ABCDEF")
    db_session.commit()
    resp = client.post(
        "/api/parent/bind",
        json={"student_id": student.id, "bind_code": "ZZZZZZ"},
        headers={"Authorization": f"Bearer {_token(account)}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "BIND_CODE_MISMATCH"
    assert db_session.query(StudentParentBinding).count() == 0


def test_bind_already_active_conflict(client, db_session):
    _, _, cls = _org(db_session)
    parent, account = _parent_account(db_session)
    student = _student(db_session, cls.id, bind_code="ABCDEF")
    db_session.commit()
    _bind(db_session, parent, student, code="OLD")
    resp = client.post(
        "/api/parent/bind",
        json={"student_id": student.id, "bind_code": "ABCDEF"},
        headers={"Authorization": f"Bearer {_token(account)}"},
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "BINDING_EXISTS"
    assert db_session.query(StudentParentBinding).count() == 1


def test_bind_reuses_inactive_history(client, db_session):
    _, _, cls = _org(db_session)
    parent, account = _parent_account(db_session)
    student = _student(db_session, cls.id, bind_code="ABCDEF")
    db_session.commit()
    old = _bind(db_session, parent, student, status=ParentBindingStatus.inactive, code="OLD")
    resp = client.post(
        "/api/parent/bind",
        json={"student_id": student.id, "bind_code": "ABCDEF", "relation": "mother"},
        headers={"Authorization": f"Bearer {_token(account)}"},
    )
    assert resp.status_code == 200
    assert db_session.query(StudentParentBinding).count() == 1  # 不新建
    db_session.refresh(old)
    assert old.status == ParentBindingStatus.active
    assert old.bind_code == "ABCDEF"
    assert old.relation == ParentBindingRelation.mother


def test_bind_student_not_found(client, db_session):
    parent, account = _parent_account(db_session)
    resp = client.post(
        "/api/parent/bind",
        json={"student_id": 999999, "bind_code": "ABCDEF"},
        headers={"Authorization": f"Bearer {_token(account)}"},
    )
    assert resp.status_code == 404


# ---- 1.4 解绑 ----

def test_unbind_own_binding(client, db_session):
    _, _, cls = _org(db_session)
    parent, account = _parent_account(db_session)
    student = _student(db_session, cls.id)
    db_session.commit()
    binding = _bind(db_session, parent, student)
    resp = client.delete(
        f"/api/parent/bind/{binding.id}",
        headers={"Authorization": f"Bearer {_token(account)}"},
    )
    assert resp.status_code == 200
    db_session.refresh(binding)
    assert binding.status == ParentBindingStatus.inactive


def test_unbind_others_binding_rejected(client, db_session):
    _, _, cls = _org(db_session)
    parent_a, account_a = _parent_account(db_session, phone="13900000001")
    parent_b, account_b = _parent_account(db_session, phone="13900000002")
    student = _student(db_session, cls.id)
    db_session.commit()
    binding = _bind(db_session, parent_a, student)
    resp = client.delete(
        f"/api/parent/bind/{binding.id}",
        headers={"Authorization": f"Bearer {_token(account_b)}"},
    )
    assert resp.status_code == 404  # 越权与不存在统一 404，不泄露
    db_session.refresh(binding)
    assert binding.status == ParentBindingStatus.active  # 记录不变


# ---- 1.5 children ----

def test_children_returns_active_bindings(client, db_session):
    _, _, cls = _org(db_session)
    parent, account = _parent_account(db_session)
    s1 = _student(db_session, cls.id, name="张三", bind_code="AAAAAA")
    s2 = _student(db_session, cls.id, name="李四", bind_code="BBBBBB")
    db_session.commit()
    _bind(db_session, parent, s1)
    _bind(db_session, parent, s2)
    # inactive 绑定不计入
    _bind(db_session, parent, _student(db_session, cls.id, name="王五", bind_code="CCCCCC"), status=ParentBindingStatus.inactive)
    resp = client.get("/api/parent/children", headers={"Authorization": f"Bearer {_token(account)}"})
    assert resp.status_code == 200
    names = {c["name"] for c in resp.json()["children"]}
    assert names == {"张三", "李四"}
    for c in resp.json()["children"]:
        assert "class_name" in c and "student_id" in c


def test_children_empty(client, db_session):
    parent, account = _parent_account(db_session)
    resp = client.get("/api/parent/children", headers={"Authorization": f"Bearer {_token(account)}"})
    assert resp.status_code == 200
    assert resp.json()["children"] == []
