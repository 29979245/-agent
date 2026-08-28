"""change parent-backend 2.5/2.6：周报端点（懒生成/去重/手动/no_data）+ AI 解读。

LLM 客户端经 monkeypatch 注入，避免真实网络调用。
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token, hash_password
from app.db.models import (
    Account,
    Class,
    ExamRecord,
    ExamStatus,
    ExamType,
    Grade,
    Parent,
    Question,
    School,
    Student,
    StudentAnswer,
    StudentParentBinding,
)
from app.db.models.enums import (
    AccountRole,
    Difficulty,
    ParentBindingRelation,
    ParentBindingStatus,
)
from app.db.session import get_db
from app.main import app
from app.services.analytics import weekly_report_service as wrs
from app.services.diagnosis.llm_diagnosis import DiagnosisLLMError

TODAY = datetime.date(2026, 8, 27)
_VALID_JSON = (
    '{"summary": "本周完成了练习，大部分题目都做对了。", '
    '"detail": "最近在学习和氧气相关的反应。", '
    '"advice": "可以聊聊生活中的化学。", "no_data": false}'
)


def _at(day, hour=10):
    return datetime.datetime.combine(day, datetime.time(hour, 0))


@pytest.fixture()
def env(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    stu = Student(class_id=cls.id, name="张三", bind_code="ABCDEF")
    db_session.add(stu)
    db_session.flush()
    q = Question(content="题目", answer="B", knowledge_points="氧化还原反应", difficulty=Difficulty.easy)
    db_session.add(q)
    db_session.flush()
    exam = ExamRecord(
        student_id=stu.id,
        name="每日练习",
        status=ExamStatus.published,
        exam_type=ExamType.practice,
        exam_date=TODAY,
        question_stats={"mode": "daily"},
    )
    db_session.add(exam)
    db_session.flush()
    db_session.add(
        StudentAnswer(
            student_id=stu.id,
            question_id=q.id,
            exam_id=exam.id,
            answer_text="B",
            is_correct=True,
            answered_at=_at(TODAY),
        )
    )
    db_session.commit()
    parent = Parent(name="张父", phone="13900000001")
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
    db_session.add(
        StudentParentBinding(
            parent_id=parent.id,
            student_id=stu.id,
            bind_code="ABCDEF",
            relation=ParentBindingRelation.father,
            status=ParentBindingStatus.active,
        )
    )
    db_session.commit()
    return {"student_id": stu.id, "token": create_token(account.id, AccountRole.parent.value)}


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


class _MockClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture()
def llm(monkeypatch):
    mock = _MockClient([])
    monkeypatch.setattr(wrs, "FallbackDiagnosisLLMClient", lambda: mock)
    return mock


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------- 2.5 周报端点 ----------------

def test_weekly_lazy_generate(client, db_session, env, llm):
    llm.responses.append(_VALID_JSON)
    resp = client.get(f"/api/parent/child/{env['student_id']}/weekly", headers=_auth(env["token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["no_data"] is False
    assert body["summary"].startswith("本周完成了练习")
    stu = db_session.get(Student, env["student_id"])
    assert stu.weekly_report_week is not None  # 已缓存
    assert stu.weekly_report["summary"].startswith("本周完成了练习")


def test_weekly_dedup_returns_cache(client, db_session, env, llm):
    llm.responses.append(_VALID_JSON)
    first = client.get(f"/api/parent/child/{env['student_id']}/weekly", headers=_auth(env["token"]))
    assert first.status_code == 200
    calls_after_first = llm.calls
    second = client.get(f"/api/parent/child/{env['student_id']}/weekly", headers=_auth(env["token"]))
    assert second.status_code == 200
    assert second.json() == first.json()  # 周内去重返回缓存
    assert llm.calls == calls_after_first  # 未重复调用 LLM


def test_weekly_manual_generate(client, db_session, env, llm):
    llm.responses.append(_VALID_JSON)
    resp = client.post(
        f"/api/parent/child/{env['student_id']}/weekly/generate", headers=_auth(env["token"])
    )
    assert resp.status_code == 200
    assert resp.json()["no_data"] is False


def test_weekly_no_data(client, db_session, env, llm):
    # 无作答 → no_data 分支，不调用 LLM
    stu = db_session.get(Student, env["student_id"])
    db_session.query(StudentAnswer).filter(StudentAnswer.student_id == stu.id).delete()
    db_session.commit()
    resp = client.get(f"/api/parent/child/{env['student_id']}/weekly", headers=_auth(env["token"]))
    assert resp.status_code == 200
    assert resp.json()["no_data"] is True
    assert resp.json()["summary"] == "本周暂无练习记录"
    assert llm.calls == 0


def test_weekly_generate_failure_readable_error(client, db_session, env, llm):
    llm.responses.append(DiagnosisLLMError("boom"))
    resp = client.get(f"/api/parent/child/{env['student_id']}/weekly", headers=_auth(env["token"]))
    assert resp.status_code == 500
    assert resp.json()["error_code"] == "WEEKLY_REPORT_FAILED"
    stu = db_session.get(Student, env["student_id"])
    assert stu.weekly_report_week is None  # 保持未生成状态以便重试


# ---------------- 2.6 AI 通俗解读 ----------------

def test_ai_summary_success(client, db_session, env, llm):
    llm.responses.append('{"summary": "孩子最近状态不错，继续加油。", "no_data": false}')
    resp = client.post(
        f"/api/parent/child/{env['student_id']}/report/ai-summary",
        headers=_auth(env["token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["summary"] == "孩子最近状态不错，继续加油。"


def test_ai_summary_failure_readable_error(client, db_session, env, llm):
    llm.responses.append(DiagnosisLLMError("boom"))
    resp = client.post(
        f"/api/parent/child/{env['student_id']}/report/ai-summary",
        headers=_auth(env["token"]),
    )
    assert resp.status_code == 500
    assert resp.json()["error_code"] == "AI_SUMMARY_FAILED"
