"""出题组工具 SSE 指令发射集成测试（tasks 2.3 / 4.3）。

走 execute_tool 全链路（Guard 四层 → 执行 → 特殊字段剥离 → _emit_directives），
断言工具返回的三段式 _route 与 exam_images 事件正确转发射 SSE 事件。
"""
import asyncio

import pytest

from app.agents.tools import tools_exam
from app.agents.tools.context import ToolContext
from app.agents.tools.registry import execute_tool
from app.db.models import Question, QuestionSet


def run(coro):
    return asyncio.run(coro)


class FakeBank:
    """最小替身：关键词匹配单题。"""

    def __init__(self):
        self._questions = [{
            "ref_id": "浙/2024/卷#q1", "content": "关于 H2O 的说法",
            "options": ["A"], "answer": "A", "analysis": "水",
            "knowledge_points": ["水"], "difficulty": "easy",
            "region": "浙江", "year": 2024, "paper": "浙江卷",
        }]

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
        return {"total": len(items), "items": items[:page_size]}


class UnavailableSearch:
    available = False

    async def search(self, query, limit=5):
        return []


class FakeVector:
    available = True

    def __init__(self, hits):
        self._hits = hits

    def search(self, content, knowledge_points=None, exclude_id=None, k=5):
        return [h for h in self._hits if h["question_id"] != exclude_id][:k]


def test_save_to_bank_emits_three_part_route(db_session):
    events = []
    ctx = ToolContext(
        db=db_session,
        user={"user_id": 7, "role": "teacher"},
        emit=lambda name, payload: events.append((name, payload)),
    )
    result = run(execute_tool(ctx, "save_to_bank", {
        "bank_name": "集成库",
        "questions": [{"content": "H2O 组成", "answer": "A", "knowledge_points": ["水"],
                       "difficulty": "easy", "audit": {"passed": True}}],
        "description": "",
    }))
    names = [n for n, _ in events]
    assert names == ["navigate", "populate", "action"], str(names)
    nav = next(p for n, p in events if n == "navigate")
    assert nav["page"] == "exam-v2"
    pop = next(p for n, p in events if n == "populate")
    assert pop["target"] == "exam-set"
    assert pop["data"]["set_id"] == result["bank_id"]
    act = next(p for n, p in events if n == "action")
    assert act == {"action": "openTab", "payload": "bank"}
    assert "_route" not in result  # 指令已剥离，纯净业务结果
    assert db_session.get(QuestionSet, result["bank_id"]) is not None


def test_save_to_bank_legacy_route_compat(db_session, monkeypatch):
    """旧 {page, params} 形式仍发 navigate 事件（兼容回归）。"""
    from app.agents.tools.registry import _emit_directives

    events = []
    ctx = ToolContext(emit=lambda name, payload: events.append((name, payload)))
    _emit_directives(ctx, {"route": {"page": "exam-bank", "params": {"tab": "bank", "bank_id": 3}}}, {})
    assert [n for n, _ in events] == ["navigate"]
    assert events[0][1] == {"page": "exam-bank", "params": {"tab": "bank", "bank_id": 3}}


def test_emit_directives_string_navigate():
    """navigate 为字符串时按页名发 navigate（容错，防 page=None 泄漏）。"""
    from app.agents.tools.registry import _emit_directives

    events = []
    ctx = ToolContext(emit=lambda name, payload: events.append((name, payload)))
    _emit_directives(ctx, {"route": {"navigate": "exam-bank", "params": {"tab": "bank"}}}, {})
    assert [n for n, _ in events] == ["navigate"]
    assert events[0][1] == {"page": "exam-bank", "params": {"tab": "bank"}}


def test_search_exam_bank_emits_exam_images(monkeypatch):
    events = []
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector([
        {"question_id": 5, "content": "看图题", "answer": "A", "knowledge_points": ["图像"],
         "image_url": "http://img/5.png", "similarity": 0.9},
    ]))
    ctx = ToolContext(
        bank=FakeBank(),
        search=UnavailableSearch(),
        emit=lambda name, payload: events.append((name, payload)),
    )
    result = run(execute_tool(ctx, "search_exam_bank", {"keyword": "看图题目", "count": 3}))
    img = [p for n, p in events if n == "exam_images"]
    assert img and "http://img/5.png" in img[0]["urls"]
    assert "image_url" not in result  # tool_result 不含图片字段
    assert any(i["ref_id"] == "向量#5" for i in result["items"])
