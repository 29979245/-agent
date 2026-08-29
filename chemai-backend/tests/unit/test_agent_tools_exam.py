"""出题组 7 工具单测（tasks 3.x）：三级搜索 / 联网 / 面板 / AI 出题 / 存库 / 列表 / 删除。"""
import asyncio

import pytest

from app.agents.tools import tools_exam
from app.agents.tools.context import ToolContext
from app.db.models import Question, QuestionSet
from app.services.question.historical import HistoricalBank
from app.services.question.vector import get_vector


def run(coro):
    return asyncio.run(coro)


# ---------------- 假对象 ----------------

class FakeBank:
    """HistoricalBank 最小替身：2 题，支持 keyword/year 过滤。"""

    def __init__(self):
        self._questions = [
            {
                "ref_id": "浙/2024/卷#q1",
                "content": "下列关于 H2O 的说法正确的是", "options": ["A", "B", "C", "D"],
                "answer": "A", "analysis": "水是氧化物", "knowledge_points": ["水"],
                "difficulty": "easy", "region": "浙江", "year": 2024, "paper": "浙江卷",
            },
            {
                "ref_id": "浙/2023/卷#q2",
                "content": "配平：Na + Cl2 → NaCl", "options": [], "answer": "2Na + Cl2 → 2NaCl",
                "analysis": "化合价升降", "knowledge_points": ["氧化还原"],
                "difficulty": "medium", "region": "浙江", "year": 2023, "paper": "浙江卷",
            },
        ]

    def search(self, keyword=None, region=None, year=None, page=1, page_size=20):
        items = []
        for q in self._questions:
            text = f"{q['content']} {' '.join(q['knowledge_points'])}"
            if keyword and keyword not in text:
                continue
            if region and q["region"] != region:
                continue
            if year and q["year"] != year:
                continue
            items.append(q)
        return {"total": len(items), "page": page, "page_size": page_size, "items": items[:page_size]}


class FakeVector:
    available = True

    def __init__(self, hits=None):
        self._hits = hits or [{"question_id": 999, "content": "向量补充题", "answer": "B", "knowledge_points": ["向量"]}]

    def search(self, content, knowledge_points=None, exclude_id=None, k=5):
        return [h for h in self._hits if h["question_id"] != exclude_id][:k]


class FakeSearch:
    available = True

    def __init__(self, results=None):
        self._results = results or [
            {"title": "t", "url": "http://x", "snippet": "联网补充内容"},
            {"title": "u", "url": "http://y", "snippet": "更多内容"},
        ]

    async def search(self, query, limit=5):
        return self._results[:limit]


class UnavailableSearch:
    available = False

    async def search(self, query, limit=5):
        return []


class FakeLLM:
    def __init__(self, text):
        self._text = text

    async def acomplete_chain(self, messages):
        return self._text


# ---------------- search_exam_bank ----------------

def test_search_exam_bank_tier1_local(monkeypatch):
    ctx = ToolContext(bank=FakeBank())
    out = run(tools_exam.search_exam_bank(ctx, keyword="水", count=3))
    assert out["total"] == 1
    assert out["items"][0]["ref_id"] == "浙/2024/卷#q1"
    assert out["ai_supplemented"] is False


def test_search_exam_bank_tier2_vector(monkeypatch):
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector())
    ctx = ToolContext(bank=FakeBank())
    out = run(tools_exam.search_exam_bank(ctx, keyword="氧化还原", count=3))
    assert any("向量" in i["ref_id"] for i in out["items"])
    assert out["total"] >= 1


def test_search_exam_bank_tier3_web_ai_mark(monkeypatch):
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[]))
    ctx = ToolContext(bank=FakeBank(), search=FakeSearch())
    out = run(tools_exam.search_exam_bank(ctx, keyword="不存在关键词", count=3))
    assert out["ai_supplemented"] is True
    assert "AI辅助搜索" in out["note"]
    assert any(i["ref_id"].startswith("web#") for i in out["items"])


def test_search_exam_bank_no_web_when_unavailable():
    ctx = ToolContext(bank=FakeBank(), search=UnavailableSearch())
    out = run(tools_exam.search_exam_bank(ctx, keyword="不存在关键词", count=3))
    assert out["ai_supplemented"] is False


# ---------------- web_search ----------------

def test_web_search_summary():
    ctx = ToolContext(search=FakeSearch())
    out = run(tools_exam.web_search(ctx, query="化学平衡"))
    assert out["query"] == "化学平衡"
    assert out["source_count"] == 2
    assert len(out["summary"]) <= 400


def test_web_search_unavailable():
    ctx = ToolContext(search=UnavailableSearch())
    out = run(tools_exam.web_search(ctx, query="x"))
    assert out["source_count"] == 0
    assert "未配置" in out["summary"]


def test_web_search_no_search_client():
    ctx = ToolContext()
    out = run(tools_exam.web_search(ctx, query="x"))
    assert out["source_count"] == 0


# ---------------- show_exam_workbench ----------------

def test_show_exam_workbench_directive():
    out = tools_exam.show_exam_workbench(ToolContext(), knowledge_points="氧化还原", difficulty="medium")
    assert out["_component"] == "exam-workbench"
    assert out["knowledge_points"] == ["氧化还原"]
    assert out["difficulty"] == "medium"


# ---------------- generate_questions ----------------

_GOOD_JSON = (
    '[{"content": "写出 $NaCl + AgNO3 → AgCl + NaNO3$ 的方程式", '
    '"options": [], "answer": "见解析", "analysis": "复分解反应", '
    '"knowledge_points": ["复分解反应"], "difficulty": "medium"}]'
)


def test_generate_questions_pipeline(monkeypatch):
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[]))
    ctx = ToolContext(llm=FakeLLM(_GOOD_JSON), bank=FakeBank())
    out = run(tools_exam.generate_questions(ctx, knowledge_points="复分解", difficulty="medium", count=1))
    assert out["count"] == 1
    q = out["questions"][0]
    assert "$" in q["content"]  # 化学式已包装 LaTeX
    assert q["audit"]["passed"] is True
    assert q["difficulty"] == "medium"
    assert out["_component"] == "question-preview"


def test_generate_questions_no_llm():
    ctx = ToolContext()
    out = run(tools_exam.generate_questions(ctx, knowledge_points="复分解", difficulty="medium"))
    assert out["error"] == "llm_unavailable"


def test_generate_questions_parse_failure():
    ctx = ToolContext(llm=FakeLLM("不是JSON"), bank=FakeBank())
    out = run(tools_exam.generate_questions(ctx, knowledge_points="复分解", difficulty="medium"))
    assert out["error"] == "llm_parse_failed"


def test_generate_questions_invalid_difficulty_normalized():
    ctx = ToolContext(llm=FakeLLM(_GOOD_JSON), bank=FakeBank())
    out = run(tools_exam.generate_questions(ctx, knowledge_points="复分解", difficulty="超难"))
    assert all(q["difficulty"] == "medium" for q in out["questions"])


# ---------------- save_to_bank / list_banks / delete_bank ----------------

def _teacher_ctx(db, **kw):
    return ToolContext(db=db, user={"user_id": 7, "role": "teacher"}, **kw)


def test_save_to_bank_creates_set_and_questions(db_session):
    ctx = _teacher_ctx(db_session)
    questions = [{"content": "H2O 的组成", "options": [], "answer": "A", "analysis": "x",
                  "knowledge_points": ["水"], "difficulty": "easy", "audit": {"passed": True}}]
    out = run(tools_exam.save_to_bank(ctx, bank_name="水专题", questions=questions))
    assert out["bank_id"] > 0
    assert out["question_count"] == 1
    assert out["_route"]["page"] == "exam-bank"
    qs = db_session.get(QuestionSet, out["bank_id"])
    assert qs is not None and qs.name == "水专题"
    assert db_session.query(Question).filter(Question.content == "H2O 的组成").count() == 1


def test_list_banks(db_session):
    run(tools_exam.save_to_bank(_teacher_ctx(db_session), bank_name="A库", questions=[]))
    run(tools_exam.save_to_bank(_teacher_ctx(db_session), bank_name="B库", questions=[]))
    out = tools_exam.list_banks(_teacher_ctx(db_session))
    assert out["total"] == 2
    names = {i["name"] for i in out["items"]}
    assert names == {"A库", "B库"}


def test_delete_bank(db_session):
    svc_ctx = _teacher_ctx(db_session)
    qs = run(tools_exam.save_to_bank(svc_ctx, bank_name="待删", questions=[]))
    out = tools_exam.delete_bank(svc_ctx, bank_id=qs["bank_id"])
    assert out["deleted"] is True
    assert db_session.get(QuestionSet, qs["bank_id"]) is None
