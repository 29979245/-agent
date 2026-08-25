"""题库管理 API 集成测试（3.2-3.3）：CRUD/导入/试卷树/历史真题 + teacher+ 权限门禁。"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db.models import Account, Class, Grade, Question, QuestionSet, School, Student, Teacher
from app.db.models.enums import AccountRole, Difficulty, TeacherStatus
from app.db.session import get_db
from app.main import app
from app.services.question.historical import reload_bank


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


def _account(db_session, username, role, role_id):
    acc = Account(
        username=username,
        password_hash=hash_password("Passw0rd!"),
        role=role,
        role_id=role_id,
    )
    db_session.add(acc)
    db_session.commit()
    return acc


def _teacher_token(client, db_session):
    school = _org(db_session)[0]
    teacher = Teacher(school_id=school.id, name="王老师", phone="13800000001", status=TeacherStatus.approved)
    db_session.add(teacher)
    db_session.flush()
    _account(db_session, "t_exam", AccountRole.teacher, teacher.id)
    login = client.post("/api/auth/login", json={"username": "t_exam", "password": "Passw0rd!"})
    return login.json()["access_token"]


def _student_token(client, db_session):
    _, _, cls = _org(db_session)
    student = Student(class_id=cls.id, name="张三", bind_code="123456")
    db_session.add(student)
    db_session.flush()
    _account(db_session, "s_exam", AccountRole.student, student.id)
    login = client.post("/api/auth/login", json={"username": "s_exam", "password": "Passw0rd!"})
    return login.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _seed_bank(tmp_path):
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "高考化学.json").write_text(
        '{"title": "高考化学", "questions": ['
        '{"id": "q1", "content": "配平：H2+O2→H2O", "answer": "2H2+O2=2H2O",'
        ' "knowledge_points": ["氧化还原"], "difficulty": "easy"}]}',
        encoding="utf-8",
    )
    return reload_bank(tmp_path)


# ---- CRUD ----

def test_create_list_detail(client, db_session):
    token = _teacher_token(client, db_session)
    resp = client.post("/api/exam-bank/exam-sets", headers=_auth(token),
                       json={"name": "2024 高考真题", "region": "全国卷", "year": 2024})
    assert resp.status_code == 200
    set_id = resp.json()["id"]

    resp = client.get("/api/exam-bank/exam-sets?region=全国卷&year=2024", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "2024 高考真题"
    assert body["items"][0]["is_preset"] is False

    resp = client.get(f"/api/exam-bank/exam-sets/{set_id}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["questions"] == []


def test_list_sets_filter_by_teacher_id(client, db_session):
    """GET /api/exam-bank/exam-sets?teacher_id=N 只返回该教师的文件夹（设计 §5.3）。"""
    token = _teacher_token(client, db_session)
    client.post("/api/exam-bank/exam-sets", headers=_auth(token), json={"name": "我的文件夹"})
    all_sets = client.get("/api/exam-bank/exam-sets", headers=_auth(token)).json()
    tid = all_sets["items"][0]["teacher_id"]
    filtered = client.get(f"/api/exam-bank/exam-sets?teacher_id={tid}", headers=_auth(token)).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["name"] == "我的文件夹"
    none = client.get("/api/exam-bank/exam-sets?teacher_id=99999", headers=_auth(token)).json()
    assert none["total"] == 0


def test_import_and_remove_questions_endpoint(client, db_session):
    token = _teacher_token(client, db_session)
    set_id = client.post("/api/exam-bank/exam-sets", headers=_auth(token),
                         json={"name": "基础训练"}).json()["id"]
    q1 = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    q2 = Question(content="题B", answer="b", difficulty=Difficulty.medium)
    db_session.add_all([q1, q2])
    db_session.commit()

    resp = client.post(f"/api/exam-bank/exam-sets/{set_id}/import-questions",
                       headers=_auth(token), json={"question_ids": [q1.id, q2.id]})
    assert resp.status_code == 200
    assert resp.json() == {"set_id": set_id, "added": 2, "skipped": 0, "skipped_reasons": {}}

    resp = client.delete(f"/api/exam-bank/exam-sets/{set_id}/questions/{q1.id}", headers=_auth(token))
    assert resp.status_code == 200
    assert db_session.get(Question, q1.id) is not None  # 题目保留


def test_delete_folder_keeps_questions(client, db_session):
    token = _teacher_token(client, db_session)
    set_id = client.post("/api/exam-bank/exam-sets", headers=_auth(token),
                         json={"name": "待删"}).json()["id"]
    q = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.commit()
    client.post(f"/api/exam-bank/exam-sets/{set_id}/import-questions",
                headers=_auth(token), json={"question_ids": [q.id]})
    q_id = q.id

    resp = client.delete(f"/api/exam-bank/exam-sets/{set_id}", headers=_auth(token))
    assert resp.status_code == 200
    assert db_session.get(Question, q_id) is not None
    assert client.get(f"/api/exam-bank/exam-sets/{set_id}", headers=_auth(token)).status_code == 404


def test_delete_preset_folder_forbidden(client, db_session):
    token = _teacher_token(client, db_session)
    preset = QuestionSet(name="系统预设", is_preset=True)
    db_session.add(preset)
    db_session.commit()
    resp = client.delete(f"/api/exam-bank/exam-sets/{preset.id}", headers=_auth(token))
    assert resp.status_code == 400


# ---- 试卷树 / 历史真题 ----

def test_papers_and_historical(client, db_session, tmp_path):
    _seed_bank(tmp_path)
    token = _teacher_token(client, db_session)
    resp = client.get("/api/exam-bank/papers", headers=_auth(token))
    assert resp.status_code == 200
    assert len(resp.json()["tree"]) == 1

    resp = client.get("/api/exam-bank/historical?keyword=配平&region=全国卷", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["answer"] == "2H2+O2=2H2O"


# ---- 权限门禁 ----

def test_student_forbidden_on_write(client, db_session):
    token = _student_token(client, db_session)
    resp = client.post("/api/exam-bank/exam-sets", headers=_auth(token), json={"name": "x"})
    assert resp.status_code == 403
    resp = client.get("/api/exam-bank/exam-sets", headers=_auth(token))
    assert resp.status_code == 200  # student 可读题库


def test_missing_token_unauthorized(client):
    resp = client.get("/api/exam-bank/exam-sets")
    assert resp.status_code == 401
