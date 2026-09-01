"""审核 API L2 集成测试（task 5.1-5.5 + Mode 1 批量生成 + Mode 2 手动导入）。

- 批量生成：POST /api/question/generate 参数驱动（knowledge_points[]/quantity/question_types/variant_qid/extra_requirements），
  返回 questions[]/generated_count/total_available，每题带 equation_level 四维审核报告
- 手动导入：POST /api/question/import（manual/ocr 只过方程式级硬闸，question_level=None）
- 5.2 POST /api/question/{id}/approve（passed 入库 / warning 带复核标记 / blocked 拒绝）
- 5.3 POST /api/question/{id}/regenerate（重生成 + 两层重审）
- 5.5 全部端点 teacher+ 权限（无令牌 401 / 学生 403）
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.api.v1.audit import get_review_client
from app.core.security import create_token
from app.db.models import AuditStatus, Difficulty, Question, QuestionSource
from app.db.session import get_db
from app.main import app
from app.services.question.historical import HistoricalQuestion

PASSED_REVIEW = (
    '{"scientificity": 90, "difficulty": 80, "knowledge": 85, "discrimination": 75}'
)
BLOCKED_REVIEW = (
    '{"scientificity": 50, "difficulty": 40, "knowledge": 45, "discrimination": 40}'
)

BATCH_QUESTIONS = json.dumps(
    [
        {
            "content": "配平：2H2 + O2 → 2H2O",
            "options": ["A. 化合反应", "B. 分解反应", "C. 置换反应", "D. 复分解反应"],
            "answer": "A",
            "analysis": "氢氧反应生成水，属化合反应。",
            "knowledge_points": ["氧化还原"],
            "difficulty": "medium",
            "trap_hint": "",
        },
        {
            "content": "实验室制氧气：2H2O2 → 2H2O + O2",
            "answer": "2H2O2 → 2H2O + O2",
            "analysis": "双氧水在二氧化锰催化下分解。",
            "knowledge_points": ["氧气制取"],
            "difficulty": "medium",
            "trap_hint": "",
        },
    ],
    ensure_ascii=False,
)

# LLM 把化学式 LaTeX（\underset / \xrightarrow 等）原样塞进 JSON 而漏转义反斜杠 → 非法转义修复。
# 必须用裸字符串（非 json.dumps）：json.dumps 会正确转义成合法 JSON，复现不了坏输出。
BATCH_UNESCAPED_LATEX = (
    '[{"content": "配平：\\underset{点燃}{2H2 + O2 -> 2H2O}", '
    '"options": ["A. 化合反应"], "answer": "A", '
    '"analysis": "氢氧反应生成水。", "knowledge_points": ["氧化还原"], '
    '"difficulty": "medium", "trap_hint": ""}]'
)

# \r / \t 是合法 JSON 转义（不抛异常），但会把 \rightleftharpoons 静默转成 回车+ightleftharpoons、
# \times 转成 Tab+imes。裸字符串（非 json.dumps）才能复现未转义反斜杠。
BATCH_UNESCAPED_CTRL = (
    '[{"content": "2SO2(g)+O2(g) \\rightleftharpoons 2SO3(g)，配平 \\times 计算", '
    '"options": [], "answer": "", "analysis": "", "knowledge_points": ["化学平衡"], '
    '"difficulty": "medium", "trap_hint": ""}]'
)

BATCH_UNBALANCED = json.dumps(
    [
        {
            "content": "水电解生成氢气：H2 + O2 → H2O",
            "answer": "H2 + O2 → H2O",
            "analysis": "配平有误。",
            "knowledge_points": ["水与氢气"],
            "difficulty": "medium",
            "trap_hint": "",
        }
    ],
    ensure_ascii=False,
)


class MockReviewClient:
    """评审/生成客户端：按序返回 responses，耗尽后重复末值。"""

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


# ---- 批量生成（Mode 1，设计 doc 25 §3.1）----


def test_generate_batch_returns_questions(client, db_session):
    app.dependency_overrides[get_review_client] = lambda: MockReviewClient([BATCH_QUESTIONS])
    try:
        resp = client.post(
            "/api/question/generate",
            json={
                "knowledge_points": ["氧化还原"],
                "difficulty": "medium",
                "quantity": 2,
                "question_types": ["choice", "fill"],
                "extra_requirements": "结合生活情境",
            },
            headers=_teacher_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_review_client, None)
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_count"] == 2
    assert len(body["questions"]) == 2
    assert isinstance(body["total_available"], int)
    q0 = body["questions"][0]
    assert q0["question_id"] and q0["content"]
    assert q0["overall_status"] == "passed"
    assert q0["audit_report"]["equation_level"]["overall_status"] == "passed"
    assert q0["audit_report"]["question_level"] is None  # 批量走四维方程式审核
    stored = db_session.get(Question, q0["question_id"])
    assert stored.source == QuestionSource.ai
    assert stored.audit_status == AuditStatus.passed
    assert stored.knowledge_points == "氧化还原"


def test_generate_batch_requires_knowledge_points(client, db_session):
    resp = client.post(
        "/api/question/generate",
        json={"difficulty": "medium"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 422


def test_generate_batch_quantity_capped(client, db_session):
    resp = client.post(
        "/api/question/generate",
        json={"knowledge_points": ["氧化还原"], "quantity": 20},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 422


def test_generate_batch_unbalanced_persists_blocked(client, db_session):
    app.dependency_overrides[get_review_client] = lambda: MockReviewClient([BATCH_UNBALANCED])
    try:
        resp = client.post(
            "/api/question/generate",
            json={"knowledge_points": ["水与氢气"], "quantity": 1},
            headers=_teacher_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_review_client, None)
    assert resp.status_code == 200
    body = resp.json()
    q0 = body["questions"][0]
    assert q0["overall_status"] == "blocked"
    assert q0["audit_report"]["equation_level"]["overall_status"] == "blocked"
    stored = db_session.get(Question, q0["question_id"])
    assert stored.audit_status == AuditStatus.blocked


def test_generate_batch_llm_failure_returns_500(client, db_session):
    app.dependency_overrides[get_review_client] = lambda: MockReviewClient(["no json here"])
    try:
        resp = client.post(
            "/api/question/generate",
            json={"knowledge_points": ["氧化还原"], "quantity": 1},
            headers=_teacher_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_review_client, None)
    assert resp.status_code == 500
    assert resp.json()["error_code"] == "GENERATION_FAILED"


def test_generate_batch_repairs_unescaped_latex(client, db_session):
    """LLM 输出未转义反斜杠（\\underset）时修复非法转义而非 500（回归：变体路径 500）。"""
    app.dependency_overrides[get_review_client] = lambda: MockReviewClient([BATCH_UNESCAPED_LATEX])
    try:
        resp = client.post(
            "/api/question/generate",
            json={"knowledge_points": ["氧化还原"], "quantity": 1},
            headers=_teacher_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_review_client, None)
    assert resp.status_code == 200
    q0 = resp.json()["questions"][0]
    assert "\\underset" in q0["content"]  # 修复后保留字面反斜杠，不丢失 LaTeX


def test_generate_batch_keeps_rightleftharpoons(client, db_session):
    """\\r\\t 合法转义不抛异常，但会静默吃掉反斜杠（\\rightleftharpoons→ightleftharpoons）。"""
    app.dependency_overrides[get_review_client] = lambda: MockReviewClient([BATCH_UNESCAPED_CTRL])
    try:
        resp = client.post(
            "/api/question/generate",
            json={"knowledge_points": ["化学平衡"], "quantity": 1},
            headers=_teacher_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_review_client, None)
    assert resp.status_code == 200
    content = resp.json()["questions"][0]["content"]
    # 完整命令串（\r 后跟 rightleftharpoons）在即证明未被 \r→回车 吃掉：
    # 损坏版是 <CR>ightleftharpoons，不含 "\rightleftharpoons" 字面串。
    assert "\\rightleftharpoons" in content
    assert "\\times" in content  # \t 同理不转成 Tab（损坏版是 <Tab>imes）


def test_generate_batch_variant_uses_blueprint(client, db_session, monkeypatch):
    """variant_qid → 服务端取蓝本注入 prompt，入库的是变体而非蓝本。"""
    class _FakeBank:
        def get_question(self, ref_id):
            return HistoricalQuestion(
                id="q1",
                content="蓝本：铁在氧气中燃烧 3Fe + 2O2 → Fe3O4",
                answer="3Fe + 2O2 → Fe3O4",
                knowledge_points=["金属与氧气反应"],
                difficulty="medium",
            )

    monkeypatch.setattr("app.api.v1.audit.get_bank", lambda: _FakeBank())
    app.dependency_overrides[get_review_client] = lambda: MockReviewClient([BATCH_QUESTIONS])
    try:
        resp = client.post(
            "/api/question/generate",
            json={
                "knowledge_points": ["金属与氧气反应"],
                "difficulty": "medium",
                "quantity": 1,
                "variant_qid": "2025/北京/期中#q1",
                "variant_source": "historical",
            },
            headers=_teacher_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_review_client, None)
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_count"] == 1
    stored = db_session.get(Question, body["questions"][0]["question_id"])
    assert stored.content
    assert "蓝本" not in stored.content  # 入库的是 LLM 变体而非蓝本


def test_generate_batch_variant_missing_blueprint_400(client, db_session):
    resp = client.post(
        "/api/question/generate",
        json={"knowledge_points": ["氧化还原"], "quantity": 1, "variant_qid": "不存在/题#q9"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 400


# ---- Mode 2 手动导入 / OCR（design doc 25 §3.2/§3.3）----


def test_import_manual_equation_level_only(client, db_session):
    resp = client.post(
        "/api/question/import",
        json={
            "content": "配平：2H2 + O2 → 2H2O",
            "options": [],
            "answer": "2H2 + O2 → 2H2O",
            "analysis": "解析",
            "knowledge_points": "氧化还原",
            "difficulty": "medium",
            "source": "manual",
        },
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["question_id"]
    assert body["overall_status"] == "passed"
    assert body["audit_report"]["question_level"] is None  # manual 只方程式级
    stored = db_session.get(Question, body["question_id"])
    assert stored.source == QuestionSource.manual


def test_import_ocr_skips_question_level(client, db_session):
    resp = client.post(
        "/api/question/import",
        json={"content": "2H2 + O2 → 2H2O", "source": "ocr"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["audit_report"]["question_level"] is None
    assert body["overall_status"] == "passed"
    assert db_session.get(Question, body["question_id"]).source == QuestionSource.ocr


def test_import_unbalanced_blocked(client, db_session):
    resp = client.post(
        "/api/question/import",
        json={"content": "H2 + O2 → H2O", "source": "manual"},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["overall_status"] == "blocked"
    assert db_session.get(Question, resp.json()["question_id"]).audit_status == AuditStatus.blocked


def test_import_requires_content(client, db_session):
    resp = client.post("/api/question/import", json={"source": "manual"}, headers=_teacher_headers())
    assert resp.status_code == 422


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
    ("/api/question/generate", {"knowledge_points": ["氧化还原"]}),
    ("/api/question/import", {"content": "H2 + O2 → H2O"}),
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
