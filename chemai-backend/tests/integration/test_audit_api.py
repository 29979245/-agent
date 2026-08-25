"""审核 API L2 集成测试（task 5.1-5.5）。

- 5.1 POST /api/question/audit 重新审核返回双层报告
- 5.2 POST /api/question/{id}/approve（passed 入库 / warning 带复核标记 / blocked 拒绝）
- 5.3 POST /api/question/{id}/regenerate（重生成 + 两层重审）
- 5.4 POST /api/question/generate 响应内嵌双层报告（AI 两层 / manual 只方程式级）
- 5.5 全部端点 teacher+ 权限（无令牌 401 / 学生 403）
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1.audit import get_review_client
from app.core.security import create_token
from app.db.models import Difficulty, Question, QuestionSource
from app.db.session import get_db
from app.main import app

PASSED_REVIEW = (
    '{"scientificity": 90, "difficulty": 80, "knowledge": 85, "discrimination": 75}'
)
BLOCKED_REVIEW = (
    '{"scientificity": 50, "difficulty": 40, "knowledge": 45, "discrimination": 40}'
)


class MockReviewClient:
    """评审客户端：按序返回 responses，耗尽后重复末值（支撑自动重生成循环多次评审）。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self._i = 0

    def complete(self, messages: list[dict]) -> str:
        idx = min(self._i, len(self.responses) - 1)
        self._i += 1
        return self.responses[idx]


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    def _mock_review_client():
        return MockReviewClient([PASSED_REVIEW])

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_review_client] = _mock_review_client

    def _teardown():
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_review_client, None)

    with TestClient(app) as c:
        yield c
    _teardown()


def _teacher_headers():
    token = create_token(user_id=1, role="teacher", school_id=1)
    return {"Authorization": f"Bearer {token}"}


def _student_headers():
    token = create_token(user_id=2, role="student", school_id=1)
    return {"Authorization": f"Bearer {token}"}


def _make_question(db_session, content="H2 + O2 → H2O", status="passed", source="manual"):
    q = Question(
        content=content,
        options=[],
        answer="",
        analysis="",
        knowledge_points="",
        difficulty=Difficulty.medium,
        source=QuestionSource(source),
        audit_status=status,
        audit_report={},
    )
    db_session.add(q)
    db_session.flush()
    db_session.commit()
    return q


# ---- 5.4 generate 内嵌双层报告 ----


def test_generate_ai_embeds_two_layer_report(client, db_session):
    resp = client.post(
        "/api/question/generate",
        json={
            "content": "配平：2H2 + O2 → 2H2O",
            "answer": "2H2 + O2 → 2H2O",
            "source": "ai",
        },
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["question_id"]
    report = body["audit_report"]
    assert "equation_level" in report and "question_level" in report and "overall_status" in report
    assert report["question_level"]["composite"] == 84.25
    assert report["overall_status"] == "passed"
    assert report["equation_level"]["系数配平"]["status"] == "passed"


def test_generate_manual_skips_question_level(client, db_session):
    resp = client.post(
        "/api/question/generate",
        json={"content": "H2 + O2 → H2O", "source": "manual"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    report = resp.json()["audit_report"]
    assert report["question_level"] is None
    assert report["overall_status"] == "blocked"  # 未配平硬阻断


def test_generate_ai_blocked_when_equation_unbalanced(client, db_session):
    resp = client.post(
        "/api/question/generate",
        json={"content": "H2 + O2 → H2O", "source": "ai"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_status"] == "blocked"
    assert body["audit_report"]["equation_level"]["overall_status"] == "blocked"


def test_generate_ai_auto_regenerates_to_failure(client, db_session):
    """blocked 自动重生成 ≤3 次，3 次仍 blocked → meta.generation_failed=true（spec 4.2）。"""
    resp = client.post(
        "/api/question/generate",
        json={"content": "H2 + O2 → H2O", "source": "ai"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["generation_failed"] is True
    assert body["overall_status"] == "blocked"
    meta = body["audit_report"]["meta"]
    assert meta["regeneration_attempts"] == 3
    assert meta["generation_failed"] is True
    db_session.refresh(db_session.get(Question, body["question_id"]))
    assert db_session.get(Question, body["question_id"]).audit_report["meta"][
        "generation_failed"
    ] is True


# ---- 5.1 re-audit ----


def test_audit_returns_two_layer_report(client, db_session):
    q = _make_question(db_session, content="2H2 + O2 → 2H2O", source="manual")
    resp = client.post(
        "/api/question/audit",
        json={"question_id": q.id},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["question_id"] == q.id
    assert body["overall_status"] == "passed"
    assert body["audit_report"]["equation_level"]["overall_status"] == "passed"


def test_audit_preserves_existing_meta(client, db_session):
    """重审不丢既有 meta：regeneration_attempts / generation_failed 保留（spec 4.2）。"""
    q = _make_question(db_session, content="2H2 + O2 → 2H2O", source="manual")
    q.audit_report = {
        "meta": {"regeneration_attempts": 2, "generation_failed": True, "review_flag": True}
    }
    db_session.commit()
    resp = client.post(
        "/api/question/audit",
        json={"question_id": q.id},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    meta = resp.json()["audit_report"]["meta"]
    assert meta["regeneration_attempts"] == 2
    assert meta["generation_failed"] is True
    assert meta["review_flag"] is True


def test_audit_missing_question_404(client, db_session):
    resp = client.post(
        "/api/question/audit",
        json={"question_id": 99999},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 404


# ---- 5.2 approve ----


def test_approve_passed_enters_bank(client, db_session):
    q = _make_question(db_session, status="passed")
    resp = client.post(f"/api/question/{q.id}/approve", headers=_teacher_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["review_flag"] is False
    db_session.refresh(q)
    assert q.audit_report["meta"]["review_flag"] is False


def test_approve_warning_sets_review_flag(client, db_session):
    q = _make_question(db_session, status="warning")
    resp = client.post(f"/api/question/{q.id}/approve", headers=_teacher_headers())
    assert resp.status_code == 200
    assert resp.json()["review_flag"] is True
    db_session.refresh(q)
    assert q.audit_report["meta"]["review_flag"] is True


def test_approve_persists_approved_marker(client, db_session):
    """教师批准后持久化已入库标记：meta.status == "approved"（spec 审核状态机）。"""
    q = _make_question(db_session, status="passed")
    resp = client.post(f"/api/question/{q.id}/approve", headers=_teacher_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    db_session.refresh(q)
    assert q.audit_report["meta"]["status"] == "approved"
    assert q.audit_report["meta"]["review_flag"] is False


def test_approve_blocked_rejected(client, db_session):
    q = _make_question(db_session, status="blocked")
    resp = client.post(f"/api/question/{q.id}/approve", headers=_teacher_headers())
    assert resp.status_code == 400


def test_approve_missing_question_404(client, db_session):
    resp = client.post("/api/question/99999/approve", headers=_teacher_headers())
    assert resp.status_code == 404


# ---- 5.3 regenerate ----


def test_regenerate_reaudits_and_increments_attempts(client, db_session):
    q = _make_question(db_session, content="H2 + O2 → H2O", status="blocked")
    resp = client.post(
        f"/api/question/{q.id}/regenerate",
        json={"content": "2H2 + O2 → 2H2O"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_status"] == "passed"
    assert body["audit_report"]["meta"]["regeneration_attempts"] == 1
    db_session.refresh(q)
    assert q.content == "2H2 + O2 → 2H2O"
    assert q.audit_status == "passed"


def test_regenerate_strips_latex_ce_wrapper(client, db_session):
    """regenerate 先剥离 $\\ce{...}$ 包裹再审核（回归：包裹残留污染元素计数导致误判阻断）。"""
    q = _make_question(db_session, content=r"$\ce{2H2 + O2 -> 2H2O2}$", status="blocked")
    resp = client.post(
        f"/api/question/{q.id}/regenerate",
        json={"content": r"$\ce{2H2 + O2 -> 2H2O}$"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_status"] == "passed"
    assert body["audit_report"]["equation_level"]["系数配平"]["status"] == "passed"


def test_regenerate_success_clears_generation_failed(client, db_session):
    """重生成通过后清除 generation_failed：已修复题目不应仍显示"出题失败"。"""
    q = _make_question(db_session, content="H2 + O2 → H2O", status="blocked")
    q.audit_report = {"meta": {"regeneration_attempts": 3, "generation_failed": True}}
    db_session.commit()
    resp = client.post(
        f"/api/question/{q.id}/regenerate",
        json={"content": "2H2 + O2 → 2H2O"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_status"] == "passed"
    assert body["audit_report"]["meta"]["generation_failed"] is False
    db_session.refresh(q)
    assert q.audit_report["meta"]["generation_failed"] is False


def test_regenerate_missing_question_404(client, db_session):
    resp = client.post(
        "/api/question/99999/regenerate",
        json={"content": "2H2 + O2 → 2H2O"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 404


def test_regenerate_rejects_passed(client, db_session):
    """passed 题目无需重生成（spec：仅 warning / blocked 触发重生成）。"""
    q = _make_question(db_session, status="passed")
    resp = client.post(
        f"/api/question/{q.id}/regenerate",
        json={"content": "2H2 + O2 → 2H2O"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 400


# ---- 5.5 权限 ----

AUDIT_PATHS = [
    ("/api/question/generate", {"content": "H2 + O2 → H2O"}),
    ("/api/question/audit", {"question_id": 1}),
]


def test_audit_endpoints_require_token(client, db_session):
    for path, body in AUDIT_PATHS:
        resp = client.post(path, json=body)
        assert resp.status_code == 401, f"{path} 无令牌应 401"


def test_student_forbidden_on_audit_endpoints(client, db_session):
    for path, body in AUDIT_PATHS:
        resp = client.post(path, json=body, headers=_student_headers())
        assert resp.status_code == 403, f"{path} 学生应 403"


def test_student_forbidden_on_approve(client, db_session):
    q = _make_question(db_session, status="passed")
    resp = client.post(f"/api/question/{q.id}/approve", headers=_student_headers())
    assert resp.status_code == 403
