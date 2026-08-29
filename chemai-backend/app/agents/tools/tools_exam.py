"""出题与题库工具组（doc 30 §3.2，7 工具）——搜索真题/联网/面板/AI 出题/存库/列表/删除。

契约要点：
- search_exam_bank 三级搜索：本地关键词 → 向量召回 → 联网补齐（<3 条时 AI 标记）。
- generate_questions：RAG 检索 → LLM 生成 → 化学式标准化 → 四维审核 → 返回。
- 所有工具返回 dict；`_component` / `_route` 为前端指令，由 Guard 层剥离（doc 30 §5.3）。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import BaseModel, Field

from app.db.models import AuditStatus, Difficulty, Question, QuestionSource
from app.services.audit.equation import audit_equation, extract_equations
from app.services.question.exam_bank import ExamBankService
from app.services.question.historical import get_bank
from app.services.question.serializers import split_knowledge_points
from app.services.question.vector import get_vector, sync_question_to_vector
from app.agents.tools.chem_formula import normalize_chem_formulas
from app.agents.tools.context import ToolContext

VALID_DIFFICULTIES = {d.value for d in Difficulty}

# ---------------------------------------------------------------- 参数 Schema

class SearchExamBankArgs(BaseModel):
    keyword: str = Field(..., description="搜索关键词（>2 字符）")
    year: Optional[int] = Field(default=None, description="年份过滤（可选）")
    difficulty: Optional[str] = Field(default=None, description="难度过滤（可选）")
    count: int = Field(default=3, ge=1, le=10, description="返回数量")


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="搜索查询词")


class ShowExamWorkbenchArgs(BaseModel):
    knowledge_points: Optional[str] = Field(default=None, description="知识点（可选，预填）")
    difficulty: Optional[str] = Field(default=None, description="难度（可选，预填）")
    question_type: Optional[str] = Field(default=None, description="题型（可选，预填）")
    source: Optional[str] = Field(default=None, description="变体来源（可选，预填）")
    bank_name: Optional[str] = Field(default=None, description="目标题库名（可选，预填）")


class GenerateQuestionsArgs(BaseModel):
    knowledge_points: str = Field(..., description="知识点，逗号分隔")
    difficulty: str = Field(..., description="难度：easy/medium/hard/competition")
    count: int = Field(default=3, ge=1, le=10, description="生成数量")
    question_type: Optional[str] = Field(default=None, description="题型（选择题/填空题等）")
    variant_question_id: Optional[str] = Field(default=None, description="变体题 ref_id（可选）")


class SaveToBankArgs(BaseModel):
    bank_name: str = Field(..., description="题库文件夹名")
    questions: list[dict] = Field(..., description="题目列表（generate_questions 输出）")
    description: str = Field(default="", description="题库描述")


class ListBanksArgs(BaseModel):
    pass


class DeleteBankArgs(BaseModel):
    bank_id: int = Field(..., description="题库 ID")


# ---------------------------------------------------------------- 工具实现

def _teacher_id(ctx: ToolContext) -> int:
    """解析当前用户业务 id（JWT dict 或 UserContext 对象；无则 0=预设共享）。"""
    user = ctx.user
    if user is None:
        return 0
    if isinstance(user, dict):
        return int(user.get("user_id") or 0)
    return int(getattr(user, "user_id", 0) or 0)


async def search_exam_bank(
    ctx: ToolContext,
    keyword: str,
    year: Optional[int] = None,
    difficulty: Optional[str] = None,
    count: int = 3,
) -> dict:
    """三级搜索（doc 30 §3.2）：本地关键词 → 向量召回 → 联网补齐。"""
    bank = ctx.bank or get_bank()
    local = bank.search(keyword=keyword, year=year, page_size=count)
    items = [
        {
            "ref_id": q["ref_id"],
            "content": q["content"],
            "options": q.get("options") or [],
            "answer": q.get("answer") or "",
            "analysis": q.get("analysis") or "",
            "knowledge_points": q.get("knowledge_points") or [],
            "difficulty": q.get("difficulty") or "",
            "region": q.get("region") or "",
            "year": q.get("year"),
            "paper": q.get("paper") or "",
        }
        for q in local["items"]
    ]
    ai_supplemented = False
    note = ""

    # Tier 2：向量召回补充
    if len(items) < count and keyword:
        vec = get_vector()
        if vec.available:
            for hit in vec.search(keyword, k=count - len(items)):
                items.append({
                    "ref_id": f"向量#{hit['question_id']}",
                    "content": hit["content"],
                    "options": [],
                    "answer": hit.get("answer") or "",
                    "analysis": "",
                    "knowledge_points": hit.get("knowledge_points") or [],
                    "difficulty": "",
                    "region": "",
                    "year": None,
                    "paper": "向量召回",
                })

    # Tier 3：联网补齐（本地 < 3 且有 keyword）
    if len(items) < 3 and keyword and ctx.search is not None and ctx.search.available:
        web = await ctx.search.search(keyword, limit=3)
        if web:
            ai_supplemented = True
            note = f"AI辅助搜索（本地题库仅 {local['total']} 道，以下为AI补充）"
            for r in web:
                items.append({
                    "ref_id": f"web#{r['url'] or len(items)}",
                    "content": r.get("snippet") or r.get("title") or "",
                    "options": [],
                    "answer": "",
                    "analysis": "",
                    "knowledge_points": [],
                    "difficulty": "",
                    "region": "",
                    "year": None,
                    "paper": r.get("title") or "联网",
                })

    return {
        "total": len(items),
        "items": items[:count],
        "ai_supplemented": ai_supplemented,
        "note": note,
    }


async def web_search(ctx: ToolContext, query: str) -> dict:
    """多路联网搜索，结果摘要 ≤400 字（doc 30 §3.2）。"""
    if ctx.search is None or not ctx.search.available:
        return {"query": query, "summary": "联网搜索服务未配置（SEARCH_API_BASE / SEARCH_API_KEY）", "source_count": 0, "urls": []}
    results = await ctx.search.search(query, limit=5)
    if not results:
        return {"query": query, "summary": "未找到相关结果", "source_count": 0, "urls": []}
    summary = "；".join((r.get("snippet") or r.get("title") or "") for r in results)[:400]
    return {
        "query": query,
        "summary": summary,
        "source_count": len(results),
        "urls": [r.get("url") for r in results[:3]],
    }


def show_exam_workbench(
    ctx: ToolContext,
    knowledge_points: Optional[str] = None,
    difficulty: Optional[str] = None,
    question_type: Optional[str] = None,
    source: Optional[str] = None,
    bank_name: Optional[str] = None,
) -> dict:
    """内联渲染出题工作台面板（doc 30 §3.2）。"""
    return {
        "_component": "exam-workbench",
        "knowledge_points": [kp for kp in split_knowledge_points(knowledge_points or "")],
        "difficulty": difficulty or "medium",
        "question_type": question_type or "",
        "source": source or "",
        "bank_name": bank_name or "",
    }


# ---- AI 出题管线 ----

_GENERATE_SYSTEM_PROMPT = (
    "你是 ChemAI 的中学化学出题专家。根据给定知识点与难度生成选择题/填空题，要求：\n"
    "1. 每题包含 content、options（数组，选择题为 4 个选项）、answer、analysis、knowledge_points；\n"
    "2. 化学方程式使用 LaTeX（下标 H_{2}O）；\n"
    "3. 题目科学严谨，答案唯一，分析讲解步骤；\n"
    "4. 只输出一个 JSON 数组，不要输出任何额外文字或 markdown 围栏。"
)


def _extract_json_array(text: str) -> list[dict]:
    """从 LLM 输出稳健提取题目数组（容忍 ```json 围栏 / 包裹对象）。"""
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM 输出不含 JSON 数组")
        data = json.loads(text[start:end + 1])
    if isinstance(data, dict) and "questions" in data:
        data = data["questions"]
    if not isinstance(data, list):
        raise ValueError("LLM 输出顶层非数组")
    return data


def _coerce_difficulty(difficulty: str) -> str:
    return difficulty if difficulty in VALID_DIFFICULTIES else "medium"


def _audit_question(q: dict) -> dict:
    """四维审核：对题面方程做确定性审核（doc 30 §3.2）。返回审核摘要。"""
    equations = extract_equations(q.get("content", "") or "")
    if not equations:
        return {"passed": True, "equation_count": 0, "errors": []}
    errors: list[str] = []
    for eq in equations:
        report = audit_equation(eq)
        if report and getattr(report, "balanced", True) is False:
            errors.append(f"方程式未配平：{eq}")
    return {"passed": not errors, "equation_count": len(equations), "errors": errors}


def _rag_context(ctx: ToolContext, knowledge_points: str, count: int) -> list[dict]:
    """RAG 检索 few-shot 样例：向量优先，回落关键词。"""
    samples: list[dict] = []
    vec = get_vector()
    if vec.available:
        for hit in vec.search(knowledge_points, k=count):
            samples.append({"content": hit["content"], "answer": hit.get("answer") or ""})
    if len(samples) < count and knowledge_points:
        bank = ctx.bank or get_bank()
        for q in bank.search(keyword=knowledge_points, page_size=count)["items"]:
            samples.append({"content": q["content"], "answer": q.get("answer") or ""})
    return samples[:count]


async def generate_questions(
    ctx: ToolContext,
    knowledge_points: str,
    difficulty: str,
    count: int = 3,
    question_type: Optional[str] = None,
    variant_question_id: Optional[str] = None,
) -> dict:
    """AI 出题：RAG 检索 → LLM 生成 → 化学式标准化 → 四维审核（doc 30 §3.2）。"""
    if ctx.llm is None:
        return {"error": "llm_unavailable", "message": "LLM 未配置，无法生成题目", "_guard_error": True}
    difficulty = _coerce_difficulty(difficulty)
    samples = _rag_context(ctx, knowledge_points, count)

    prompt_parts = [f"知识点：{knowledge_points}", f"难度：{difficulty}", f"数量：{count}"]
    if question_type:
        prompt_parts.append(f"题型：{question_type}")
    if variant_question_id:
        prompt_parts.append(f"请基于真题 {variant_question_id} 生成变式")
    if samples:
        prompt_parts.append("参考样例：\n" + json.dumps(samples, ensure_ascii=False))
    messages = [
        {"role": "system", "content": _GENERATE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(prompt_parts)},
    ]
    try:
        raw = await ctx.llm.acomplete_chain(messages)
    except Exception as exc:  # noqa: BLE001 —— 上游失败给出可读错误
        return {"error": "llm_generation_failed", "message": f"生成失败：{exc}", "_guard_error": True}

    try:
        questions = _extract_json_array(raw)[:count]
    except Exception as exc:  # noqa: BLE001
        return {"error": "llm_parse_failed", "message": f"解析失败：{exc}", "_guard_error": True}

    for q in questions:
        q["knowledge_points"] = q.get("knowledge_points") or split_knowledge_points(knowledge_points)
        q["difficulty"] = _coerce_difficulty(str(q.get("difficulty") or difficulty))
        q["content"] = normalize_chem_formulas(q.get("content", "") or "")
        q["options"] = [normalize_chem_formulas(str(o)) for o in (q.get("options") or [])]
        q["analysis"] = normalize_chem_formulas(q.get("analysis", "") or "")
        q["answer"] = normalize_chem_formulas(q.get("answer", "") or "")
        q["audit"] = _audit_question(q)

    return {
        "count": len(questions),
        "difficulty": difficulty,
        "knowledge_points": split_knowledge_points(knowledge_points),
        "questions": questions,
        "_component": "question-preview",
    }


async def save_to_bank(
    ctx: ToolContext,
    bank_name: str,
    questions: list[dict],
    description: str = "",
) -> dict:
    """创建题库文件夹、逐题入库、同步向量索引（doc 30 §3.2）。"""
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    svc = ExamBankService(ctx.db)
    qs = svc.create_set(name=bank_name, teacher_id=_teacher_id(ctx), description=description)
    created_ids: list[int] = []
    for qd in questions:
        content = (qd.get("content") or "").strip()
        if not content:
            continue
        question = Question(
            content=content,
            options=qd.get("options") or [],
            answer=(qd.get("answer") or "").strip() or "（见解析）",
            analysis=qd.get("analysis") or "",
            knowledge_points=", ".join(qd.get("knowledge_points") or []),
            difficulty=Difficulty(_coerce_difficulty(str(qd.get("difficulty") or "medium"))),
            source=QuestionSource.ai,
            audit_status=AuditStatus.passed if (qd.get("audit") or {}).get("passed", True) else AuditStatus.warning,
            audit_report=(qd.get("audit") or {}),
        )
        ctx.db.add(question)
        ctx.db.flush()
        created_ids.append(question.id)
    if created_ids:
        svc.import_questions(qs.id, created_ids)
        for qid in created_ids:
            question = ctx.db.get(Question, qid)
            if question:
                sync_question_to_vector(question)
    ctx.db.commit()
    return {
        "bank_id": qs.id,
        "bank_name": qs.name,
        "question_count": len(created_ids),
        "_route": {"page": "exam-bank", "params": {"tab": "bank", "bank_id": qs.id}},
    }


def list_banks(ctx: ToolContext) -> dict:
    """列出全部题库文件夹（doc 30 §3.2）。"""
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    data = ExamBankService(ctx.db).list_sets(teacher_id=_teacher_id(ctx) or None)
    return {
        "total": data["total"],
        "items": [
            {"id": s["id"], "name": s["name"], "question_count": s["question_count"], "is_preset": s["is_preset"]}
            for s in data["items"]
        ],
    }


def delete_bank(ctx: ToolContext, bank_id: int) -> dict:
    """删除题库文件夹及题目关联（题目实体保留）。破坏性操作，需审批确认（doc 30 §3.2）。"""
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    svc = ExamBankService(ctx.db)
    qs = svc.get_set(bank_id)
    svc.delete_set(bank_id)
    ctx.db.commit()
    return {"deleted": True, "bank_id": bank_id, "name": qs.name}
