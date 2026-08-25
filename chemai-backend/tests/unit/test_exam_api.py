"""考试生命周期 API 集成测试（4.4-4.5）：全生命周期流转 200 + 非法转换 400 + 权限门禁。"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db.models import (
    Account,
    Class,
    Grade,
    Question,
    School,
    Student,
    StudentAnswer,
    Teacher,
)
from app.db.models.enums import AccountRole, AuditStatus, Difficulty, TeacherStatus
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
    acc = Account(username=username, password_hash=hash_password("Passw0rd!"), role=role, role_id=role_id)
    db_session.add(acc)
    db_session.commit()
    return acc


def _teacher_token(client, db_session):
    school = _org(db_session)[0]
    teacher = Teacher(school_id=school.id, name="王老师", phone="13800000002", status=TeacherStatus.approved)
    db_session.add(teacher)
    db_session.flush()
    _account(db_session, "t_ex", AccountRole.teacher, teacher.id)
    login = client.post("/api/auth/login", json={"username": "t_ex", "password": "Passw0rd!"})
    return login.json()["access_token"]


def _student_token(client, db_session):
    _, _, cls = _org(db_session)
    student = Student(class_id=cls.id, name="张三", bind_code="123456")
    db_session.add(student)
    db_session.flush()
    _account(db_session, "s_ex", AccountRole.student, student.id)
    login = client.post("/api/auth/login", json={"username": "s_ex", "password": "Passw0rd!"})
    return login.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_exam(client, token, cls_id):
    resp = client.post("/api/exam/create", headers=_auth(token),
                       json={"class_id": cls_id, "name": "期中化学", "exam_type": "exam"})
    assert resp.status_code == 200
    return resp.json()["exam_id"]


def _seed_bank(tmp_path):
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "高考化学.json").write_text(
        '{"title": "高考化学", "questions": ['
        '{"id": "q1", "content": "真题：离子方程式", "answer": "B",'
        ' "knowledge_points": ["离子反应"], "difficulty": "medium"}]}',
        encoding="utf-8",
    )
    return reload_bank(tmp_path)


# ---- 4.4 生命周期端点 ----

def test_create_and_full_lifecycle(client, db_session):
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    q = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.commit()
    exam_id = _create_exam(client, token, cls.id)

    # 双渠道关联
    resp = client.post(f"/api/exam/{exam_id}/questions", headers=_auth(token),
                       json={"question_ids": [q.id]})
    assert resp.status_code == 200 and resp.json()["added"] == 1

    # 发布
    resp = client.post(f"/api/exam/{exam_id}/publish", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"
    assert resp.json()["question_stats"]["question_count"] == 1

    # 阅卷 → 完成 → 归档
    resp = client.post(f"/api/exam/{exam_id}/start-grading", headers=_auth(token))
    assert resp.status_code == 200 and resp.json()["status"] == "grading"
    resp = client.post(f"/api/exam/{exam_id}/finalize", headers=_auth(token))
    assert resp.status_code == 200 and resp.json()["status"] == "completed"
    resp = client.post(f"/api/exam/{exam_id}/archive", headers=_auth(token))
    assert resp.status_code == 200 and resp.json()["status"] == "archived"


def test_channel2_historical_question(client, db_session, tmp_path):
    _seed_bank(tmp_path)
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    exam_id = _create_exam(client, token, cls.id)
    resp = client.post(f"/api/exam/{exam_id}/questions", headers=_auth(token),
                       json={"question_ids": ["全国卷/2024/高考化学#q1"]})
    assert resp.status_code == 200 and resp.json()["added"] == 1
    resp = client.get(f"/api/exam/{exam_id}/questions", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["items"][0]["content"] == "真题：离子方程式"


def test_publish_empty_exam_400(client, db_session):
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    exam_id = _create_exam(client, token, cls.id)
    resp = client.post(f"/api/exam/{exam_id}/publish", headers=_auth(token))
    assert resp.status_code == 400


def test_illegal_transition_400(client, db_session):
    """跳过阅卷直接归档 → 400。"""
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    exam_id = _create_exam(client, token, cls.id)
    resp = client.post(f"/api/exam/{exam_id}/archive", headers=_auth(token))
    assert resp.status_code == 400


def test_delete_draft_exam(client, db_session):
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    exam_id = _create_exam(client, token, cls.id)
    resp = client.delete(f"/api/exam/{exam_id}", headers=_auth(token))
    assert resp.status_code == 200
    resp = client.get(f"/api/exam/{exam_id}/questions", headers=_auth(token))
    assert resp.status_code == 404


def test_list_exams_paginated(client, db_session):
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    e1 = _create_exam(client, token, cls.id)
    e2 = _create_exam(client, token, cls.id)
    resp = client.get("/api/exam", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert body["items"][0]["exam_id"] == max(e1, e2)  # id 倒序
    assert body["items"][0]["class_name"] == "1班"
    assert body["items"][0]["status"] == "draft"
    assert body["items"][0]["question_count"] == 0
    assert "exam_date" in body["items"][0]


def test_list_classes(client, db_session):
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    resp = client.get("/api/classes", headers=_auth(token))
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()["items"]]
    assert cls.id in ids


def test_results_endpoints(client, db_session):
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    exam_id = _create_exam(client, token, cls.id)
    q = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.commit()
    client.post(f"/api/exam/{exam_id}/questions", headers=_auth(token), json={"question_ids": [q.id]})
    client.post(f"/api/exam/{exam_id}/publish", headers=_auth(token))
    client.post(f"/api/exam/{exam_id}/start-grading", headers=_auth(token))
    stu = Student(class_id=cls.id, name="张三")
    db_session.add(stu)
    db_session.flush()
    db_session.add(StudentAnswer(student_id=stu.id, question_id=q.id, exam_id=exam_id, is_correct=True))
    db_session.commit()
    client.post(f"/api/exam/{exam_id}/finalize", headers=_auth(token))

    resp = client.get(f"/api/exam/{exam_id}/results", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["students"][0]["name"] == "张三"

    resp = client.get(f"/api/exam/{exam_id}/result/{stu.id}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["answers"][0]["is_correct"] is True


def test_export_exam_docx(client, db_session):
    """导出端点：GET /api/question/export/{record_id}?with_answers=true 返回 Word 试卷。"""
    import io

    from docx import Document

    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    q = Question(content="配平：H2+O2→H2O", answer="2H2+O2=2H2O", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.commit()
    exam_id = _create_exam(client, token, cls.id)
    client.post(f"/api/exam/{exam_id}/questions", headers=_auth(token),
                json={"question_ids": [q.id]})

    resp = client.get(f"/api/question/export/{exam_id}?with_answers=true", headers=_auth(token))
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers["content-type"]
    assert 'filename="exam-' in resp.headers["content-disposition"]
    text = "\n".join(p.text for p in Document(io.BytesIO(resp.content)).paragraphs)
    assert "（含答案版）" in text  # 教师版底部标记
    assert "配平：H2+O2→H2O" in text


def test_export_exam_invalid_format_400(client, db_session):
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    exam_id = _create_exam(client, token, cls.id)
    resp = client.get(f"/api/question/export/{exam_id}?format=xlsx", headers=_auth(token))
    assert resp.status_code == 400


def test_export_exam_student_forbidden(client, db_session):
    """导出为教师专属操作：student 403。"""
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    exam_id = _create_exam(client, token, cls.id)
    stoken = _student_token(client, db_session)
    resp = client.get(f"/api/question/export/{exam_id}", headers=_auth(stoken))
    assert resp.status_code == 403


def test_approve_syncs_vector(client, db_session, monkeypatch):
    """批准入库触发向量索引同步（vector-retrieval spec：保存入库后同步）。"""
    token = _teacher_token(client, db_session)
    q = Question(
        content="配平：H2+O2→H2O",
        answer="2H2+O2=2H2O",
        difficulty=Difficulty.easy,
        audit_status=AuditStatus.passed,
        audit_report={},
    )
    db_session.add(q)
    db_session.commit()
    q_id = q.id

    from app.api.v1 import audit as audit_api

    calls = []
    monkeypatch.setattr(audit_api, "sync_question_to_vector", lambda question: calls.append(question))
    resp = client.post(f"/api/question/{q_id}/approve", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert len(calls) == 1 and calls[0].id == q_id


# ---- 权限门禁 ----

def test_student_read_only(client, db_session):
    token = _student_token(client, db_session)
    resp = client.post("/api/exam/create", headers=_auth(token),
                       json={"class_id": 1, "name": "x"})
    assert resp.status_code == 403


def test_student_cannot_read_class_or_others_results(client, db_session):
    """跨学生泄漏修复：student 读全班成绩/他人作答 403；读自身作答 200。"""
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    exam_id = _create_exam(client, token, cls.id)
    q = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.commit()
    client.post(f"/api/exam/{exam_id}/questions", headers=_auth(token), json={"question_ids": [q.id]})
    client.post(f"/api/exam/{exam_id}/publish", headers=_auth(token))
    client.post(f"/api/exam/{exam_id}/start-grading", headers=_auth(token))
    stu = Student(class_id=cls.id, name="张三")
    db_session.add(stu)
    db_session.flush()
    db_session.add(StudentAnswer(student_id=stu.id, question_id=q.id, exam_id=exam_id, is_correct=True))
    _account(db_session, "s_own", AccountRole.student, stu.id)
    other = Student(class_id=cls.id, name="李四")
    db_session.add(other)
    db_session.commit()
    login = client.post("/api/auth/login", json={"username": "s_own", "password": "Passw0rd!"})
    stoken = login.json()["access_token"]

    resp = client.get(f"/api/exam/{exam_id}/results", headers=_auth(stoken))
    assert resp.status_code == 403  # 全班总览仅教师+

    resp = client.get(f"/api/exam/{exam_id}/result/{stu.id}", headers=_auth(stoken))
    assert resp.status_code == 200  # 自身作答可读
    assert resp.json()["answers"][0]["is_correct"] is True

    resp = client.get(f"/api/exam/{exam_id}/result/{other.id}", headers=_auth(stoken))
    assert resp.status_code == 403  # 他人作答不可读


def test_student_questions_without_answer_key(client, db_session):
    """答案泄漏修复：student 读卷不带 answer/analysis；teacher 保留完整。"""
    token = _teacher_token(client, db_session)
    _, _, cls = _org(db_session)
    exam_id = _create_exam(client, token, cls.id)
    q = Question(content="题A", answer="正确答案", analysis="解析", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.commit()
    client.post(f"/api/exam/{exam_id}/questions", headers=_auth(token), json={"question_ids": [q.id]})

    resp = client.get(f"/api/exam/{exam_id}/questions", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["items"][0]["answer"] == "正确答案"

    stu = Student(class_id=cls.id, name="张三")
    db_session.add(stu)
    db_session.flush()
    _account(db_session, "s_q", AccountRole.student, stu.id)
    db_session.commit()
    login = client.post("/api/auth/login", json={"username": "s_q", "password": "Passw0rd!"})
    stoken = login.json()["access_token"]

    resp = client.get(f"/api/exam/{exam_id}/questions", headers=_auth(stoken))
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["content"] == "题A"
    assert "answer" not in item
    assert "analysis" not in item
