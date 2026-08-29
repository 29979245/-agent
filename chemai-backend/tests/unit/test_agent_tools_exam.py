"""出题组 7 工具单测（tasks 3.x）：三级搜索 / 联网 / 面板 / AI 出题 / 存库 / 列表 / 删除。"""
import asyncio

import pytest

from app.agents.tools import tools_exam
from app.agents.tools.context import ToolContext
from app.db.models import AuditStatus, Question, QuestionSet
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
        if hits is None:
            hits = [{"question_id": 999, "content": "向量补充题", "answer": "B", "knowledge_points": ["向量"]}]
        self._hits = hits

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
    assert out["types"] == tools_exam._DEFAULT_WORKBENCH_TYPES


def test_show_exam_workbench_types_array():
    out = tools_exam.show_exam_workbench(
        ToolContext(), types=[{"val": "选择题", "active": True, "qty": 2}, {"val": "计算题", "active": False, "qty": 1}])
    assert out["types"] == [{"val": "选择题", "active": True, "qty": 2}, {"val": "计算题", "active": False, "qty": 1}]


def test_show_exam_workbench_legacy_question_type():
    out = tools_exam.show_exam_workbench(ToolContext(), question_type="填空题")
    assert out["types"] == [{"val": "填空题", "active": True, "qty": 1}]


# ---------------- generate_questions ----------------

_GOOD_JSON = (
    '[{"content": "写出化学方程式：$NaCl + AgNO3 → AgCl + NaNO3$", '
    '"options": [], "answer": "见解析", "analysis": "复分解反应", '
    '"knowledge_points": ["复分解反应"], "difficulty": "medium", "trap_hint": "忽略 AgCl 沉淀符号"}]'
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
    assert q["trap_hint"] == "忽略 AgCl 沉淀符号"  # 陷阱提示保留
    assert q["is_from_rag"] is False  # 无检索样例 → 非 RAG
    assert out["mode"] == "B"
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
    assert out["skipped_count"] == 0
    route = out["_route"]
    assert route["navigate"] == {"page": "exam-v2", "params": {}}
    assert route["populate"]["target"] == "exam-set"
    assert route["populate"]["data"] == {"set_id": out["bank_id"], "set_name": "水专题"}
    assert route["actions"] == [{"action": "openTab", "payload": "bank"}]
    qs = db_session.get(QuestionSet, out["bank_id"])
    assert qs is not None and qs.name == "水专题"
    assert db_session.query(Question).filter(Question.content == "H2O 的组成").count() == 1


def test_save_to_bank_skips_failed_audit(db_session):
    ctx = _teacher_ctx(db_session)
    questions = [
        {"content": "合格题", "options": [], "answer": "A", "audit": {"passed": True}},
        {"content": "不合格题", "options": [], "answer": "B", "audit": {"passed": False}},
    ]
    out = run(tools_exam.save_to_bank(ctx, bank_name="混合", questions=questions))
    assert out["question_count"] == 1
    assert out["skipped_count"] == 1
    assert db_session.query(Question).filter(Question.content == "不合格题").count() == 0


def test_save_to_bank_skips_empty_content(db_session):
    """空 content 题目跳过入库并计入 skipped_count（评审 #c4）。"""
    ctx = _teacher_ctx(db_session)
    questions = [
        {"content": "合格题", "options": [], "answer": "A", "audit": {"passed": True}},
        {"content": "   ", "options": [], "answer": "B", "audit": {"passed": True}},
    ]
    out = run(tools_exam.save_to_bank(ctx, bank_name="空题库", questions=questions))
    assert out["question_count"] == 1
    assert out["skipped_count"] == 1
    assert db_session.query(Question).filter(Question.content == "合格题").count() == 1


def test_save_to_bank_warning_audit_status(db_session):
    """warning 题（软标记）入库且 audit_status=warning，不被跳过（doc 26 §6.3）。"""
    ctx = _teacher_ctx(db_session)
    questions = [{
        "content": "配平：CH4 + 2O2 → CO2 + 2H2O", "options": [], "answer": "见解析",
        "knowledge_points": ["燃烧"], "difficulty": "medium",
        "audit": tools_exam._audit_question({"content": "配平：CH4 + 2O2 → CO2 + 2H2O"}),
    }]
    out = run(tools_exam.save_to_bank(ctx, bank_name="warning库", questions=questions))
    assert out["question_count"] == 1
    assert out["skipped_count"] == 0
    q = db_session.query(Question).filter(Question.content.like("%CH4%")).one()
    assert q.audit_status == AuditStatus.warning


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


# ---------------- 搜索契约升级（D6） ----------------

def test_search_exam_bank_filters_source_region(monkeypatch):
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[]))
    ctx = ToolContext(bank=FakeBank(), search=UnavailableSearch())
    out = run(tools_exam.search_exam_bank(ctx, keyword="水", count=3, source="real", region="浙江"))
    assert out["total"] == 1
    assert out["items"][0]["ref_id"] == "浙/2024/卷#q1"
    out2 = run(tools_exam.search_exam_bank(ctx, keyword="水", count=3, region="广东"))
    assert out2["total"] == 0
    out3 = run(tools_exam.search_exam_bank(ctx, keyword="水", count=3, knowledge_point="氧化还原"))
    assert out3["total"] == 0  # q1 知识点为"水"，被 knowledge_point 过滤
    out4 = run(tools_exam.search_exam_bank(ctx, keyword="水", count=3, knowledge_point="水"))
    assert out4["total"] == 1
    out5 = run(tools_exam.search_exam_bank(ctx, keyword="水", count=3, source="vector"))
    assert out5["total"] == 0  # 无向量命中，real 被 source 过滤


def test_search_exam_bank_vector_threshold(monkeypatch):
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[
        {"question_id": 1, "content": "低相似题", "answer": "A", "knowledge_points": ["x"], "similarity": 0.4},
        {"question_id": 2, "content": "合格相似题", "answer": "B", "knowledge_points": ["y"], "similarity": 0.6},
    ]))
    ctx = ToolContext(bank=FakeBank(), search=UnavailableSearch())
    out = run(tools_exam.search_exam_bank(ctx, keyword="无本地匹配词", count=3))
    refs = [i["ref_id"] for i in out["items"]]
    assert "向量#1" not in refs
    assert "向量#2" in refs


def test_search_exam_bank_vector_threshold_missing_sim_allowed(monkeypatch):
    """无相似度（ChromaDB 降级关键词路径）命中放行，不适用阈值（doc 25 §7.4）。"""
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[
        {"question_id": 3, "content": "降级关键词命中", "answer": "C", "knowledge_points": ["y"]},  # 无 similarity
    ]))
    ctx = ToolContext(bank=FakeBank(), search=UnavailableSearch())
    out = run(tools_exam.search_exam_bank(ctx, keyword="无本地匹配词", count=3))
    refs = [i["ref_id"] for i in out["items"]]
    assert "向量#3" in refs


def test_search_exam_bank_emits_exam_images(monkeypatch):
    events = []
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[
        {"question_id": 5, "content": "看图题", "answer": "A", "knowledge_points": ["图像"],
         "image_url": "http://img/5.png", "similarity": 0.9},
    ]))
    ctx = ToolContext(bank=FakeBank(), search=UnavailableSearch(), emit=lambda name, payload: events.append((name, payload)))
    out = run(tools_exam.search_exam_bank(ctx, keyword="看图", count=3))
    img_events = [p for n, p in events if n == "exam_images"]
    assert img_events and "http://img/5.png" in img_events[0]["urls"]
    assert all("image_url" not in i for i in out["items"])  # tool_result 不含图片字段


def test_search_exam_bank_image_filtered_out_no_emit(monkeypatch):
    """被 source 过滤掉的含图向量项，其 URL 不得发射（评审 #c2）。"""
    events = []
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[
        {"question_id": 5, "content": "看图题", "answer": "A", "knowledge_points": ["图像"],
         "image_url": "http://img/5.png", "similarity": 0.9},
    ]))
    ctx = ToolContext(bank=FakeBank(), search=UnavailableSearch(), emit=lambda name, payload: events.append((name, payload)))
    out = run(tools_exam.search_exam_bank(ctx, keyword="看图", count=3, source="real"))
    img_events = [p for n, p in events if n == "exam_images"]
    assert img_events == []  # 含图项被 source=real 过滤掉，事件不发射
    assert out["total"] == 0


# ---------------- 生成链路升级（D2 + D3） ----------------

def test_audit_question_balance_blocks_equation():
    """仅系数配平失败硬阻断（doc 26 §6.3）。"""
    eq = "H2 + O2 → H2O"
    audit = tools_exam._audit_question({"content": f"配平：{eq}"})
    assert audit["passed"] is False
    assert any("balance" in e for e in audit["errors"])
    assert audit["dimensions"][eq]["balance"]["status"] == "blocked"
    assert audit["dimensions"][eq]["balance"]["evidence"] != []  # 有元素差异证据


def test_audit_question_warning_does_not_block():
    """条件/产物/结构问题软标记 warning，不阻断（doc 26 §6.3）。"""
    eq = "CH4 + 2O2 → CO2 + 2H2O"  # 配平正确，但燃烧缺"点燃"条件
    audit = tools_exam._audit_question({"content": f"配平：{eq}"})
    assert audit["passed"] is True
    assert audit["errors"] == []
    assert audit["dimensions"][eq]["balance"]["status"] == "passed"
    assert audit["dimensions"][eq]["condition"]["status"] != "passed"


def test_audit_question_dimension_evidence():
    """每维返回判定与 evidence（规格：四维审核 SHALL 每维返回判定与 evidence）。"""
    eq = "CH4 + 2O2 → CO2 + 2H2O"
    audit = tools_exam._audit_question({"content": f"配平：{eq}"})
    dims = audit["dimensions"][eq]
    assert all(isinstance(d["evidence"], list) for d in dims.values())
    assert dims["balance"]["evidence"] == []  # 配平正确 → 无差异证据
    assert dims["condition"]["evidence"] != []  # 燃烧缺条件 → 有缺失条件证据


def test_generate_questions_balance_blocks(monkeypatch):
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[]))
    payload = ('[{"content": "配平：H2 + O2 → H2O", "options": [], "answer": "见解析", '
               '"analysis": "x", "knowledge_points": ["燃烧"], "difficulty": "medium"}]')
    ctx = ToolContext(llm=FakeLLM(payload), bank=FakeBank())
    out = run(tools_exam.generate_questions(ctx, knowledge_points="燃烧", difficulty="medium", count=1))
    assert out["questions"][0]["audit"]["passed"] is False


def test_generate_questions_rag_metadata(monkeypatch):
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[
        {"question_id": 123, "content": "氧化还原真题", "answer": "C",
         "knowledge_points": ["氧化还原"], "similarity": 0.9},
    ]))
    ctx = ToolContext(llm=FakeLLM(_GOOD_JSON), bank=FakeBank())
    out = run(tools_exam.generate_questions(ctx, knowledge_points="氧化还原", difficulty="medium", count=1))
    q = out["questions"][0]
    assert q["is_from_rag"] is True
    assert q["source_question_id"] == "123"
    assert q["match_method"] == "vector"
    assert q["similarity"] == 0.9


def test_generate_questions_mode_a_with_enough_samples(monkeypatch):
    monkeypatch.setattr(tools_exam, "get_vector", lambda: FakeVector(hits=[
        {"question_id": 1, "content": "样1", "answer": "A", "knowledge_points": ["z"]},
        {"question_id": 2, "content": "样2", "answer": "B", "knowledge_points": ["z"]},
        {"question_id": 3, "content": "样3", "answer": "C", "knowledge_points": ["z"]},
    ]))
    ctx = ToolContext(llm=FakeLLM(_GOOD_JSON), bank=FakeBank())
    out = run(tools_exam.generate_questions(ctx, knowledge_points="氧化还原", difficulty="medium", count=3))
    assert out["mode"] == "A"
    assert out["questions"][0]["is_from_rag"] is True


def test_generate_questions_mode_b_no_samples():
    ctx = ToolContext(llm=FakeLLM(_GOOD_JSON), bank=FakeBank())
    out = run(tools_exam.generate_questions(ctx, knowledge_points="不存在的知识点xyz", difficulty="medium", count=1))
    assert out["mode"] == "B"
    assert out["questions"][0]["is_from_rag"] is False


def test_generate_questions_trap_hint_default_empty():
    payload = ('[{"content": "无陷阱题", "options": [], "answer": "A", "analysis": "x", '
               '"knowledge_points": ["水"], "difficulty": "easy"}]')
    ctx = ToolContext(llm=FakeLLM(payload), bank=FakeBank())
    out = run(tools_exam.generate_questions(ctx, knowledge_points="水", difficulty="easy", count=1))
    assert out["questions"][0]["trap_hint"] == ""
