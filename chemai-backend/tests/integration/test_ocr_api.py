"""OCR 批改判卷 API L2 集成测试（task 7.1-7.4）。

覆盖：批量上传（空文件 400 / 格式 415 / 超限 413 / 建批）、批次状态聚合、任务重试（幂等）、
教师任务列表、三引擎服务状态、触发批改（无 done 404 / bank / teacher / self）、
分档落库（模式1 展开 StudentAnswer + 未注册跳过 + 触发诊断；模式2/3 只落 StudentSubmission）、
查询批改结果、学生越权 403。
"""
import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.diagnosis import get_diagnosis_client
from app.config import settings
from app.core.security import create_token
from app.db.base import Base
from app.db.models import (
    Account,
    Class,
    ExamRecord,
    Grade,
    Question,
    School,
    Student,
    StudentAnswer,
    StudentSubmission,
    Teacher,
    UploadSession,
)
from app.db.models.enums import (
    AccountRole,
    Difficulty,
    ExamStatus,
    ExamType,
    OCRTaskStatus,
    UploadSessionStatus,
)
from app.db.models.ocr import OCRTask
from app.db.session import get_db
from app.main import app

_TODAY = datetime.date.today()
_MAX = 10 * 1024 * 1024


class _DiagClient:
    """诊断 LLM 客户端桩：计数 + 返回合法诊断 JSON（8.1 触发断言）。"""

    def __init__(self):
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return (
            '{"barrier_type": "concept", "reasoning": "概念混淆", '
            '"confidence": 0.8, "suggestion": "复习原理"}'
        )


@pytest.fixture()
def ocr_client(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    monkeypatch.setattr(settings, "ocr_upload_dir", str(tmp_path / "ocr_uploads"))

    diag = _DiagClient()

    def _override_db():
        yield session

    def _override_diag():
        return diag

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_diagnosis_client] = _override_diag

    with TestClient(app) as c:
        yield c, session, diag

    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


# ---------------- 种子数据 ----------------

def _school(db):
    s = School(name="S", current_semester="2026-1")
    db.add(s)
    db.flush()
    return s


def _org(db, school):
    g = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(g)
    db.flush()
    cls = Class(grade_id=g.id, name="1班")
    db.add(cls)
    db.flush()
    return g, cls


def _teacher(db, school, name="王老师"):
    t = Teacher(school_id=school.id, name=name, phone=f"13{name.encode().hex()[:8]}")
    db.add(t)
    db.flush()
    acc = Account(username=f"t{name}", password_hash="x", role=AccountRole.teacher, role_id=t.id)
    db.add(acc)
    db.flush()
    return t, acc


def _student(db, cls, name, student_no=""):
    s = Student(class_id=cls.id, name=name, student_no=student_no, barrier_profile={})
    db.add(s)
    db.flush()
    return s


def _student_account(db, school):
    _, cls = _org(db, school)
    stu = _student(db, cls, "学生甲")
    acc = Account(username=f"stu{stu.id}", password_hash="x", role=AccountRole.student, role_id=stu.id)
    db.add(acc)
    db.flush()
    return acc


def _auth(account, role="teacher", school_id=1):
    token = create_token(user_id=account.id, role=role, school_id=school_id)
    return {"Authorization": f"Bearer {token}"}


def _session(db, status=UploadSessionStatus.uploaded, created_at=None, school_id=1):
    s = UploadSession(status=status, created_at=created_at, school_id=school_id)
    db.add(s)
    db.flush()
    return s


def _task(db, session, status=OCRTaskStatus.pending, result=None):
    t = OCRTask(session_id=session.id, file_path="card.png", status=status, result=result or {})
    db.add(t)
    db.flush()
    return t


def _exam(db, answers=("A", "B"), cls=None):
    exam = ExamRecord(
        class_id=cls.id if cls else None, name="期中", status=ExamStatus.published,
        exam_type=ExamType.exam, exam_date=_TODAY,
    )
    db.add(exam)
    db.flush()
    for i, ans in enumerate(answers):
        db.add(
            Question(
                content=f"第{i+1}题", options=[], answer=ans, analysis="",
                knowledge_points="", difficulty=Difficulty.medium, record_id=exam.id,
            )
        )
    db.flush()
    return exam


def _done_result(student_no="2023001234", name="张三"):
    return {
        "student_no": student_no,
        "student_name": name,
        "answers": [{"question_no": 1, "answer": "a"}, {"question_no": 2, "answer": "B"}],
    }


# ---------------- 7.1 批量上传 / 聚合 / 重试 / 列表 ----------------

def test_batch_upload_empty_files_400(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    r = client.post("/api/ocr/tasks/batch", files=[], headers=_auth(acc))
    assert r.status_code == 400
    assert db.query(UploadSession).count() == 0


def test_batch_upload_creates_pending_tasks(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    files = [
        ("files", ("card1.png", b"fake-png-1", "image/png")),
        ("files", ("card2.png", b"fake-png-2", "image/png")),
    ]
    r = client.post("/api/ocr/tasks/batch", files=files, headers=_auth(acc))
    assert r.status_code == 200
    body = r.json()
    batch_id = body["batch_id"]
    assert body["count"] == 2
    assert len(body["task_ids"]) == 2
    assert body["status"] == "uploaded"
    # 聚合
    agg = client.get(f"/api/ocr/tasks/batch/{batch_id}", headers=_auth(acc)).json()
    assert agg["total"] == 2
    assert agg["pending"] == 2
    assert [t["title"] for t in agg["tasks"]] == ["答题卡_01", "答题卡_02"]
    # 文件已落盘
    for task in db.query(OCRTask).all():
        assert task.file_path


def test_batch_upload_rejects_bad_extension_415(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    r = client.post(
        "/api/ocr/tasks/batch",
        files=[("files", ("card.txt", b"hello", "text/plain"))],
        headers=_auth(acc),
    )
    assert r.status_code == 415
    assert db.query(UploadSession).count() == 0  # 无半成品批次


def test_batch_upload_rejects_oversize_413(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    r = client.post(
        "/api/ocr/tasks/batch",
        files=[("files", ("big.png", b"x" * (_MAX + 1), "image/png"))],
        headers=_auth(acc),
    )
    assert r.status_code == 413
    assert db.query(UploadSession).count() == 0


def test_batch_status_aggregates_counts(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db, UploadSessionStatus.grading)
    _task(db, s, OCRTaskStatus.done)
    _task(db, s, OCRTaskStatus.failed)
    _task(db, s, OCRTaskStatus.pending)
    db.commit()
    body = client.get(f"/api/ocr/tasks/batch/{s.id}", headers=_auth(acc)).json()
    assert body["status"] == "grading"
    assert body["total"] == 3
    assert body["done"] == 1
    assert body["failed"] == 1
    assert body["pending"] == 1
    assert body["processing"] == 0


def test_retry_failed_task_resets_to_pending(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    t = _task(db, s, OCRTaskStatus.failed, result={"student_no": "x"})
    t.error = "OCR engine timeout"
    db.commit()
    r = client.post(f"/api/ocr/tasks/{t.id}/retry", headers=_auth(acc))
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    db.refresh(t)
    assert t.status == OCRTaskStatus.pending
    assert t.error == ""
    assert t.result == {}


def test_retry_missing_task_404(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    r = client.post("/api/ocr/tasks/999/retry", headers=_auth(acc))
    assert r.status_code == 404


def test_teacher_task_list_ordered_desc(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s1 = _session(db, created_at=datetime.datetime(2026, 8, 1))
    _task(db, s1, OCRTaskStatus.done)
    s2 = _session(db, created_at=datetime.datetime(2026, 8, 2))
    _task(db, s2, OCRTaskStatus.pending)
    db.commit()
    batches = client.get("/api/ocr/tasks", headers=_auth(acc)).json()["batches"]
    assert [b["batch_id"] for b in batches] == [s2.id, s1.id]
    assert batches[0]["tasks"][0]["status"] == "pending"


def test_services_status_structure(ocr_client, monkeypatch):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    monkeypatch.setattr(settings, "baidu_ocr_api_key", "ak")
    monkeypatch.setattr(settings, "baidu_ocr_secret_key", "sk")
    monkeypatch.setattr(settings, "llm_api_key", "lk")
    body = client.get("/api/ocr/services/status", headers=_auth(acc)).json()
    assert set(body) == {"ocr", "mineru", "vision"}
    for key in body:
        assert set(body[key]) == {"available"}
        assert isinstance(body[key]["available"], bool)
    assert body["ocr"]["available"] is True  # baidu keys 已配置
    assert body["vision"]["available"] is True  # LLM key 已配置
    assert body["mineru"]["available"] is False  # MinerU 未安装占位


def test_ocr_endpoints_forbid_student(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    stu = _student_account(db, school)
    db.commit()
    r = client.get("/api/ocr/tasks", headers=_auth(stu, role="student"))
    assert r.status_code == 403


# ---------------- 7.2 触发批改 / 分档落库 / 查询 ----------------

def test_grading_run_no_done_task_404(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    _task(db, s, OCRTaskStatus.pending)
    db.commit()
    r = client.post("/api/grading/run", json={"batch_id": s.id}, headers=_auth(acc))
    assert r.status_code == 404


def test_grading_run_bank_mode_grades_items(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    _, cls = _org(db, school)
    exam = _exam(db, answers=("A", "B"), cls=cls)
    s = _session(db)
    t = _task(db, s, OCRTaskStatus.done, result=_done_result())
    db.commit()
    r = client.post(
        "/api/grading/run", json={"batch_id": s.id, "exam_id": exam.id}, headers=_auth(acc)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "bank"
    assert body["graded"] == 1
    assert body["tasks"][0]["student_no"] == "2023001234"
    assert [i["is_correct"] for i in body["tasks"][0]["items"]] == [True, True]
    # 会话推进到 grading，结果落库
    agg = client.get(f"/api/ocr/tasks/batch/{s.id}", headers=_auth(acc)).json()
    assert agg["status"] == "grading"
    db.refresh(t)
    assert t.result["grading"]["source"] == "bank"


def test_grading_run_self_mode_when_no_answers(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result={"answers": [{"question_no": 1, "answer": "H2O"}]})
    db.commit()
    body = client.post("/api/grading/run", json={"batch_id": s.id}, headers=_auth(acc)).json()
    assert body["source"] == "self"
    item = body["tasks"][0]["items"][0]
    assert item["is_correct"] is None
    assert item["review_needed"] is True


def test_grading_run_teacher_answers_mode(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result={"answers": [{"question_no": 1, "answer": "a"}]})
    db.commit()
    r = client.post(
        "/api/grading/run",
        json={"batch_id": s.id, "teacher_answers": {"1": "A"}},
        headers=_auth(acc),
    )
    assert r.status_code == 200
    assert r.json()["source"] == "teacher"
    assert r.json()["tasks"][0]["items"][0]["is_correct"] is True


def test_grading_save_mode1_expands_and_skips_unregistered(ocr_client):
    client, db, diag = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    _, cls = _org(db, school)
    registered = _student(db, cls, "张三", student_no="2023001234")
    _student(db, cls, "李四", student_no="2023005678")
    exam = _exam(db, answers=("A", "B"), cls=cls)
    s = _session(db)
    wrong = _done_result()
    wrong["answers"][1]["answer"] = "C"  # 第二题答错（触发诊断 candidate）
    _task(db, s, OCRTaskStatus.done, result=wrong)
    _task(db, s, OCRTaskStatus.done, result=_done_result(student_no="unknown", name="待识别"))
    db.commit()
    client.post("/api/grading/run", json={"batch_id": s.id, "exam_id": exam.id}, headers=_auth(acc))
    r = client.post(
        "/api/grading/save", json={"batch_id": s.id, "exam_id": exam.id}, headers=_auth(acc)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 1
    assert body["skipped"] == 1
    assert body["submissions"] == 0
    assert body["status"] == "done"
    # 只展开注册学生的 StudentAnswer（未注册跳过，设计 11.3）
    answers = db.query(StudentAnswer).all()
    assert len(answers) == 2
    assert {a.student_id for a in answers} == {registered.id}
    assert {a.exam_id for a in answers} == {exam.id}
    assert {a.is_correct for a in answers} == {True, False}
    # 模式1 保存成功触发诊断（错误作答 candidate）
    assert diag.calls == 1


def test_grading_save_mode3_self_writes_submission_only(ocr_client):
    client, db, diag = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result={"answers": [{"question_no": 1, "answer": "H2O"}]})
    db.commit()
    client.post("/api/grading/run", json={"batch_id": s.id}, headers=_auth(acc))
    r = client.post("/api/grading/save", json={"batch_id": s.id}, headers=_auth(acc))
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 0
    assert body["skipped"] == 0
    assert body["submissions"] == 1
    assert body["status"] == "done"
    assert db.query(StudentAnswer).count() == 0
    sub = db.query(StudentSubmission).one()
    assert sub.exam_id is None  # 模式2/3 无考试可空
    assert sub.answer_list[0]["review_needed"] is True
    assert diag.calls == 0  # 无考试 → 不触发诊断


def test_grading_results_query(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result=_done_result())
    db.commit()
    client.post("/api/grading/run", json={"batch_id": s.id}, headers=_auth(acc))
    body = client.get(f"/api/grading/results/{s.id}", headers=_auth(acc)).json()
    assert body["batch_id"] == s.id
    assert body["tasks"][0]["student_name"] == "张三"
    assert body["tasks"][0]["grading"]["source"] == "self"
    assert body["submissions"] == []


# ---------------- 8.3 修复回归：越权/幂等/错误体/边界 ----------------

@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("post", "/api/ocr/tasks/batch", {"files": []}),
        ("get", "/api/ocr/tasks/batch/1", {}),
        ("post", "/api/ocr/tasks/1/retry", {}),
        ("get", "/api/ocr/tasks", {}),
        ("get", "/api/ocr/services/status", {}),
        ("post", "/api/grading/run", {"json": {"batch_id": 1}}),
        ("post", "/api/grading/save", {"json": {"batch_id": 1}}),
        ("get", "/api/grading/results/1", {}),
    ],
)
def test_student_forbidden_across_all_ocr_endpoints(ocr_client, method, path, kwargs):
    client, db, _ = ocr_client
    school = _school(db)
    stu = _student_account(db, school)
    db.commit()
    r = getattr(client, method)(path, headers=_auth(stu, role="student"), **kwargs)
    assert r.status_code == 403


def test_batch_missing_404(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    r = client.get("/api/ocr/tasks/batch/999", headers=_auth(acc))
    assert r.status_code == 404
    assert r.json()["error_code"] == "NOT_FOUND"


def test_grading_results_missing_404(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    r = client.get("/api/grading/results/999", headers=_auth(acc))
    assert r.status_code == 404


def test_cross_school_batch_forbidden_403(ocr_client):
    client, db, _ = ocr_client
    school_a = _school(db)
    _, acc_a = _teacher(db, school_a)
    school_b = _school(db)
    _, acc_b = _teacher(db, school_b, name="李老师")
    s = _session(db, school_id=school_a.id)
    _task(db, s)
    db.commit()
    denied = client.get(f"/api/ocr/tasks/batch/{s.id}", headers=_auth(acc_b, school_id=school_b.id))
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "BATCH_SCOPE_MISMATCH"
    ok = client.get(f"/api/ocr/tasks/batch/{s.id}", headers=_auth(acc_a, school_id=school_a.id))
    assert ok.status_code == 200


def test_cross_school_exam_forbidden_403(ocr_client):
    client, db, _ = ocr_client
    school_a = _school(db)
    _, acc_a = _teacher(db, school_a)
    school_b = _school(db)
    _, acc_b = _teacher(db, school_b, name="李老师")
    _, cls_b = _org(db, school_b)
    exam_b = _exam(db, cls=cls_b)
    s = _session(db, school_id=school_a.id)
    _task(db, s, OCRTaskStatus.done, result=_done_result())
    db.commit()
    r = client.post(
        "/api/grading/run",
        json={"batch_id": s.id, "exam_id": exam_b.id},
        headers=_auth(acc_a, school_id=school_a.id),
    )
    assert r.status_code == 403
    assert r.json()["error_code"] == "EXAM_SCOPE_MISMATCH"


def test_teacher_task_list_scoped_by_school(ocr_client):
    client, db, _ = ocr_client
    school_a = _school(db)
    _, acc_a = _teacher(db, school_a)
    school_b = _school(db)
    _, acc_b = _teacher(db, school_b, name="李老师")
    sa = _session(db, school_id=school_a.id)
    _task(db, sa)
    sb = _session(db, school_id=school_b.id)
    _task(db, sb)
    db.commit()
    batches_a = client.get("/api/ocr/tasks", headers=_auth(acc_a, school_id=school_a.id)).json()["batches"]
    assert [b["batch_id"] for b in batches_a] == [sa.id]
    batches_b = client.get("/api/ocr/tasks", headers=_auth(acc_b, school_id=school_b.id)).json()["batches"]
    assert [b["batch_id"] for b in batches_b] == [sb.id]


def test_teacher_task_list_empty_when_no_batches(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    assert client.get("/api/ocr/tasks", headers=_auth(acc)).json()["batches"] == []


def test_grading_run_after_save_conflict_409(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result=_done_result())
    db.commit()
    assert client.post("/api/grading/run", json={"batch_id": s.id}, headers=_auth(acc)).status_code == 200
    assert client.post("/api/grading/save", json={"batch_id": s.id}, headers=_auth(acc)).status_code == 200
    r = client.post("/api/grading/run", json={"batch_id": s.id}, headers=_auth(acc))
    assert r.status_code == 409
    assert r.json()["error_code"] == "BATCH_ALREADY_SAVED"


def test_grading_double_save_conflict_409(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result={"answers": [{"question_no": 1, "answer": "H2O"}]})
    db.commit()
    client.post("/api/grading/run", json={"batch_id": s.id}, headers=_auth(acc))
    first = client.post("/api/grading/save", json={"batch_id": s.id}, headers=_auth(acc))
    assert first.status_code == 200
    second = client.post("/api/grading/save", json={"batch_id": s.id}, headers=_auth(acc))
    assert second.status_code == 409
    assert second.json()["error_code"] == "BATCH_ALREADY_SAVED"
    assert db.query(StudentSubmission).count() == 1  # 幂等守卫，未重复落库


def test_grading_save_before_run_400(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result=_done_result())
    db.commit()
    r = client.post("/api/grading/save", json={"batch_id": s.id}, headers=_auth(acc))
    assert r.status_code == 400
    assert r.json()["error_code"] == "OCR_INCOMPLETE_GRADING"
    assert db.query(StudentSubmission).count() == 0
    db.refresh(s)
    assert s.status == UploadSessionStatus.uploaded  # 未推进到 done


def test_grading_save_incomplete_grading_400(ocr_client):
    """重试后的 done 任务缺 grading → 阻止保存，避免静默漏存。"""
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result={**_done_result(), "grading": {"source": "bank", "items": []}})
    _task(db, s, OCRTaskStatus.done, result=_done_result())  # 重试后 done 但无 grading
    db.commit()
    r = client.post("/api/grading/save", json={"batch_id": s.id}, headers=_auth(acc))
    assert r.status_code == 400
    assert r.json()["error_code"] == "OCR_INCOMPLETE_GRADING"


def test_grading_run_exam_binding_mismatch_409(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    _, cls = _org(db, school)
    exam_a = _exam(db, answers=("A",), cls=cls)
    exam_b = _exam(db, answers=("B",), cls=cls)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result=_done_result())
    db.commit()
    assert client.post(
        "/api/grading/run", json={"batch_id": s.id, "exam_id": exam_a.id}, headers=_auth(acc)
    ).status_code == 200
    r = client.post(
        "/api/grading/run", json={"batch_id": s.id, "exam_id": exam_b.id}, headers=_auth(acc)
    )
    assert r.status_code == 409
    assert r.json()["error_code"] == "EXAM_BINDING_MISMATCH"


def test_grading_save_exam_binding_mismatch_409(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    _, cls = _org(db, school)
    exam_a = _exam(db, answers=("A",), cls=cls)
    exam_b = _exam(db, answers=("B",), cls=cls)
    s = _session(db)
    _task(db, s, OCRTaskStatus.done, result=_done_result())
    db.commit()
    client.post(
        "/api/grading/run", json={"batch_id": s.id, "exam_id": exam_a.id}, headers=_auth(acc)
    )
    r = client.post(
        "/api/grading/save", json={"batch_id": s.id, "exam_id": exam_b.id}, headers=_auth(acc)
    )
    assert r.status_code == 409
    assert r.json()["error_code"] == "EXAM_BINDING_MISMATCH"


def test_batch_upload_mixed_rollback_removes_files(ocr_client):
    """批量中任一文件校验失败：回滚批次并清理已落盘文件，无孤儿文件。"""
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    upload_dir = settings.ocr_upload_dir
    r = client.post(
        "/api/ocr/tasks/batch",
        files=[
            ("files", ("good.png", b"png-data", "image/png")),
            ("files", ("bad.txt", b"text", "text/plain")),
        ],
        headers=_auth(acc),
    )
    assert r.status_code == 415
    assert db.query(UploadSession).count() == 0
    assert list(Path(upload_dir).iterdir()) == []


def test_error_body_has_unified_shape(ocr_client):
    client, db, _ = ocr_client
    school = _school(db)
    _, acc = _teacher(db, school)
    db.commit()
    r = client.post(
        "/api/ocr/tasks/batch",
        files=[("files", ("a.txt", b"x", "text/plain"))],
        headers=_auth(acc),
    )
    assert r.status_code == 415
    assert r.json()["error_code"] == "UNSUPPORTED_FILE_FORMAT"
    assert "suggestion" in r.json()
