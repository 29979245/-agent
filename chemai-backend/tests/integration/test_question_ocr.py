"""题目图片识别 API 集成测试（设计 doc 25 §3.3 Mode 3 务实版）。

- POST /api/question/ocr-recognize：上传题目图片 → 复用识别链 extract_document →
  返回题面文本 + provider/partial/degraded（可编辑预览前置）
- 权限：无令牌 401 / 学生 403；非白名单扩展名 415；缺文件 422
- _repair_ocr_text：丢反斜杠 LaTeX 命令残片自动补回
"""
import pytest
from fastapi.testclient import TestClient

from app.api.v1.audit import _ocr_text_to_katex, _repair_ocr_text
from app.core.security import create_token
from app.db.session import get_db
from app.main import app
from app.services.ocr.engines.base import OCRDocument, OCRResult


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


def _teacher_headers():
    token = create_token(user_id=1, role="teacher", school_id=1)
    return {"Authorization": f"Bearer {token}"}


def _student_headers():
    token = create_token(user_id=2, role="student", school_id=1)
    return {"Authorization": f"Bearer {token}"}


async def _fake_extract(document: OCRDocument) -> OCRResult:
    return OCRResult(text="配平：2H2 + O2 → 2H2O", provider="baidu", confidence=0.9)


def test_ocr_recognize_returns_text(client, db_session, monkeypatch):
    monkeypatch.setattr("app.api.v1.audit.extract_document", _fake_extract)
    resp = client.post(
        "/api/question/ocr-recognize",
        files={"file": ("q.png", b"fake-image-bytes", "image/png")},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "配平：2H2 + O2 → 2H2O"
    assert body["provider"] == "baidu"
    assert body["partial"] is False


def test_ocr_recognize_partial_surfaces_flags(client, db_session, monkeypatch):
    async def _partial_extract(_doc):
        return OCRResult(text="", provider="baidu", partial=True, degraded=True, error="未识别到有效文本")

    monkeypatch.setattr("app.api.v1.audit.extract_document", _partial_extract)
    resp = client.post(
        "/api/question/ocr-recognize",
        files={"file": ("q.png", b"blurry", "image/png")},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["partial"] is True
    assert body["degraded"] is True
    assert body["error"]


def test_ocr_recognize_requires_token(client, db_session):
    resp = client.post(
        "/api/question/ocr-recognize",
        files={"file": ("q.png", b"fake", "image/png")},
    )
    assert resp.status_code == 401


def test_ocr_recognize_forbidden_for_student(client, db_session):
    resp = client.post(
        "/api/question/ocr-recognize",
        files={"file": ("q.png", b"fake", "image/png")},
        headers=_student_headers(),
    )
    assert resp.status_code == 403


def test_ocr_recognize_rejects_unsupported_ext(client, db_session):
    resp = client.post(
        "/api/question/ocr-recognize",
        files={"file": ("q.txt", b"not an image", "text/plain")},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 415


def test_ocr_recognize_requires_file(client, db_session):
    resp = client.post("/api/question/ocr-recognize", headers=_teacher_headers())
    assert resp.status_code == 422


# ---- _repair_ocr_text：丢反斜杠 LaTeX 残片自动补回 ----

def test_repair_restores_dropped_backslash_harpoon():
    assert (
        _repair_ocr_text("2SO2(g)+O2(g)ightleftharpoons2SO3(g)")
        == "2SO2(g)+O2(g)\\rightleftharpoons2SO3(g)"
    )


def test_repair_does_not_double_fix_correct_command():
    assert _repair_ocr_text("2SO2 \\rightleftharpoons 2SO3") == "2SO2 \\rightleftharpoons 2SO3"


def test_repair_restores_brace_command_and_other_arrows():
    assert _repair_ocr_text("ce{H2O} 2H2+O2 rightarrow 2H2O") == "\\ce{H2O} 2H2+O2 \\rightarrow 2H2O"


def test_repair_leaves_prose_untouched():
    assert _repair_ocr_text("下列说法正确的是") == "下列说法正确的是"


def test_ocr_recognize_repairs_text(client, db_session, monkeypatch):
    async def _noisy_extract(_doc):
        return OCRResult(text="2SO2(g)+O2(g)ightleftharpoons2SO3(g)", provider="baidu")

    monkeypatch.setattr("app.api.v1.audit.extract_document", _noisy_extract)
    resp = client.post(
        "/api/question/ocr-recognize",
        files={"file": ("q.png", b"fake-image-bytes", "image/png")},
        headers=_teacher_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "$\\ce{2SO2(g)+O2(g)<=>2SO3(g)}$"


# ---- _ocr_text_to_katex：纯化学式行自动包 $\\ce{...}$ ----

def test_to_katex_wraps_unicode_arrow_equation():
    assert _ocr_text_to_katex("2SO₂(g)+O₂(g)⇌2SO₃(g)") == "$\\ce{2SO2(g)+O2(g)<=>2SO3(g)}$"


def test_to_katex_converts_repaired_latex_arrow():
    assert _ocr_text_to_katex("2SO2(g)+O2(g)\\rightleftharpoons2SO3(g)") == (
        "$\\ce{2SO2(g)+O2(g)<=>2SO3(g)}$"
    )


def test_to_katex_keeps_prose_and_mixed_lines():
    assert _ocr_text_to_katex("下列说法正确的是") == "下列说法正确的是"
    assert _ocr_text_to_katex("下列说法正确的是\n2SO2(g)+O2(g)⇌2SO3(g)") == (
        "下列说法正确的是\n$\\ce{2SO2(g)+O2(g)<=>2SO3(g)}$"
    )


def test_to_katex_no_double_wrap():
    assert _ocr_text_to_katex("$\\ce{H2O}$") == "$\\ce{H2O}$"


def test_to_katex_rejects_arrow_without_digits():
    assert _ocr_text_to_katex("rate -> increase") == "rate -> increase"
