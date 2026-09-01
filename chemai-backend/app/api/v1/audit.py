"""审核工作流 API（audit-engine-api）。

挂载于 /api/question，端点全部 teacher+ 权限（require_permission("question", ...)）。
- POST /generate       参数驱动批量生成（设计 doc 25 Mode 1）：RAG/变体蓝本 → LLM 生成 → 四维审核 → 入库
- POST /import         手动录入 / OCR 单题（设计 Mode 2/3；manual/ocr 只过方程式级硬闸）
- POST /audit          对已存储题目重新审核，返回双层报告
- POST /{id}/approve   教师批准：passed 入库 / warning 带复核标记放行
- POST /{id}/regenerate 重生成 + 两层重审

审核触发范围按题目来源区分（设计 D7）：question.source == "ai" 走两层审核；
manual / ocr 只过方程式级硬闸（题目级跳过）。批量生成按设计 §3.1.3 走四维方程式审核。
"""
import io
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.tools.chem_formula import normalize_chem_formulas
from app.config import settings
from app.core.exceptions import NotFoundError, ServerError
from app.core.permissions import permission_checker, require_permission
from app.db.models import ExamRecord, Question
from app.db.models.enums import AuditStatus, Difficulty, QuestionSource
from app.db.session import get_db
from app.services.audit.equation import audit_equation, extract_equations
from app.services.audit.review import (
    FallbackReviewLLMClient,
    ReviewLLMClient,
    ReviewLLMError,
)
from app.services.audit.state_machine import AuditState, apply_action
from app.services.audit.workflow import build_audit_report, run_content_audit
from app.services.ocr.batch import save_upload
from app.services.ocr.engines.base import OCRDocument
from app.services.ocr.engines.router import extract_document
from app.services.question.export import export_paper_docx, render_paper_pdf
from app.services.question.historical import get_bank
from app.services.question.serializers import question_dict
from app.services.question.vector import get_vector, sync_question_to_vector

audit_logger = logging.getLogger("chemai.audit")
audit_router = APIRouter()


def get_review_client() -> ReviewLLMClient:
    """题目级评审客户端（FastAPI 依赖，测试可覆盖注入 Mock）。"""
    return FallbackReviewLLMClient()


class QuestionGenerateRequest(BaseModel):
    """批量生成请求（设计 doc 25 §3.1.1 QuestionGenerateRequest）+ 额外要求/题干约束。"""

    exam_type: str = ""  # 考试类型描述，仅入 prompt
    knowledge_points: list[str] = Field(..., min_length=1)
    difficulty: str = "medium"
    quantity: int = Field(default=10, ge=1, le=10)
    barrier_type: str = ""  # concept/reading/expression，仅入 prompt
    question_types: list[str] = ["choice"]  # choice/fill/calc/experiment/inference
    variant_source: str = ""  # "historical"
    variant_qid: str = ""  # 变体蓝本 ref_id（如 "2025/北京/期中#q1"）
    extra_requirements: str = ""  # 额外要求/题干约束


class ImportRequest(BaseModel):
    """手动录入 / OCR 单题（设计 doc 25 §3.2 Mode 2）。"""

    content: str = Field(..., min_length=1)
    options: list[str] = []
    answer: str = ""
    analysis: str = ""
    knowledge_points: str | list[str] = ""  # 兼容 str 与 list 两种入参
    difficulty: str = "medium"
    source: str = "manual"  # manual / ocr


class AuditRequest(BaseModel):
    question_id: int


class RegenerateRequest(BaseModel):
    content: str | None = None
    answer: str | None = None
    analysis: str | None = None
    knowledge_points: str | None = None


def _question_data(question: Question) -> dict:
    return {
        "content": question.content,
        "options": question.options or [],
        "answer": question.answer,
        "analysis": question.analysis or "",
        "knowledge_points": question.knowledge_points or "",
        "difficulty": question.difficulty.value if question.difficulty else "medium",
    }


# ---- 批量生成管线（设计 doc 25 §3.1.2 / §3.1.3）----

VALID_DIFFICULTIES = {d.value for d in Difficulty}
_VECTOR_SIM_THRESHOLD = 0.6  # 向量召回相似度阈值（doc 25 §7.4）

_QUESTION_TYPE_RULES = {
    "choice": "选择题须提供 A/B/C/D 四个选项并标注正确选项，设置 1-2 个陷阱选项",
    "fill": "填空题用下划线标记填空位置，答案仅含填空内容，每道题可有 1-3 个空",
    "calc": "计算题给出具体数值条件，答案须含分步计算（公式、数据代入、结果、结论）",
    "experiment": "实验题描述化学实验情境并包含 2-3 个子问题",
    "inference": "推断题给出物质转化线索，要求学生推断未知物质并书写方程式",
}

_GENERATE_SYSTEM_PROMPT = (
    "你是 ChemAI 的资深中学化学教师。根据给定知识点、难度与题型生成若干道化学题。\n"
    "题型格式要求：\n{type_rules}\n"
    "化学式格式规范（所有题目与选项必须遵守）：\n"
    "1. 所有化学式、离子式和方程式必须用 $...$ LaTeX 行内公式包裹；\n"
    "2. 下标用 _ 表示、上标用 ^ 表示，元素符号首字母大写；\n"
    "3. 方程式箭头用 \\rightarrow，可逆反应用 \\rightleftharpoons；\n"
    "4. 反应条件用 \\xrightarrow{条件}，加热用 \\xrightarrow{\\triangle}；\n"
    "5. 中文正文中嵌入的化学式也必须用 $ 包裹。\n"
    "每题输出 JSON 对象："
    '{"content": "...", "type": "choice", "options": ["A. ...", ...], "answer": "...", '
    '"analysis": "...", "knowledge_points": ["..."], "difficulty": "easy", "trap_hint": "..."}，'
    "options 仅选择题需要；只输出一个 JSON 数组，不要任何额外文字或 markdown 围栏。"
)


def _coerce_difficulty(difficulty: str) -> str:
    return difficulty if difficulty in VALID_DIFFICULTIES else "medium"


def _repair_json_escapes(text: str) -> str:
    """修复 LLM JSON 输出中的非法/歧义反斜杠转义，保留合法转义。

    化学式 LaTeX（\\underset、\\xrightarrow、\\_2 等）常被 LLM 原样塞进 JSON 字符串
    而未转义反斜杠。\\r / \\t / \\b / \\f 在 JSON 里是合法转义（回车/Tab/退格/换页），
    json.loads 不报错，但会把 \\rightleftharpoons 静默转成 <CR>ightleftharpoons、
    \\times 转成 <Tab>imes——这些字符在 LaTeX 里其实是命令开头，必须按字面量补反斜杠。
    \\n（换行）保留为合法转义：LLM 大量使用且无 LaTeX 化学命令以 n 开头，歧义可忽略。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            out.append("\\\\")
            i += 1
            continue
        nxt = text[i + 1]
        if nxt == "\\":
            out.append("\\\\")
            i += 2
            continue
        if nxt in '"\\/n':
            out.append("\\" + nxt)
            i += 2
            continue
        if nxt == "u":
            hex4 = text[i + 2:i + 6]
            if len(hex4) == 4 and all(c in "0123456789abcdefABCDEF" for c in hex4):
                out.append(text[i:i + 6])
                i += 6
                continue
            out.append("\\\\u")
            i += 2
            continue
        out.append("\\\\" + nxt)
        i += 2
    return "".join(out)


def _extract_questions(text: str) -> list[dict]:
    """从 LLM 输出稳健提取题目数组（容忍 ```json 围栏 / 顶层对象包裹 / 非法转义）。"""
    text = re.sub(r"```(?:json)?", "", text).strip()
    # 先修转义再解析：\r\t\b\f 是合法 JSON 转义（不抛异常），但会把 \rightleftharpoons 静默
    # 转成 回车+ightleftharpoons、\times 转成 Tab+imes——必须按 LaTeX 字面量先行修复。
    text = _repair_json_escapes(text)
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


def _rag_samples(knowledge_points: list[str], quantity: int) -> tuple[list[dict], int]:
    """RAG 检索 few-shot 样例：向量优先，回落关键词（doc 25 §3.1.3 step0）。返回 (样例, 匹配真题总数)。"""
    keyword = " ".join(knowledge_points)
    samples: list[dict] = []
    vec = get_vector()
    if vec.available:
        for hit in vec.search(keyword, k=quantity):
            sim = hit.get("similarity")
            if sim is not None and sim < _VECTOR_SIM_THRESHOLD:
                continue
            samples.append({
                "content": hit["content"],
                "answer": hit.get("answer") or "",
                "is_from_rag": True,
                "source_question_id": str(hit.get("question_id") or ""),
                "similarity": sim,
                "match_method": "vector",
            })
    total_available = len(samples)
    if len(samples) < quantity and knowledge_points:
        res = get_bank().search(keyword=keyword, page_size=quantity)
        total_available = res["total"]
        for q in res["items"]:
            samples.append({
                "content": q["content"],
                "answer": q.get("answer") or "",
                "is_from_rag": True,
                "source_question_id": q.get("ref_id") or "",
                "similarity": None,
                "match_method": "keyword",
            })
    return samples[:quantity], total_available


def _resolve_blueprint(variant_qid: str) -> dict:
    """变体蓝本查询（设计 doc 25 §4.2）：服务端取完整信息注入 prompt。"""
    blueprint = get_bank().get_question(variant_qid)
    if blueprint is None:
        raise HTTPException(status_code=400, detail="变体蓝本题不存在")
    return {
        "content": blueprint.content,
        "answer": blueprint.answer or "",
        "knowledge_points": blueprint.knowledge_points or [],
        "difficulty": blueprint.difficulty or "medium",
    }


def _build_generate_messages(
    payload: QuestionGenerateRequest, samples: list[dict], blueprint: dict | None
) -> list[dict]:
    """组装生成 prompt（设计 §3.1.2 三层：System 题型占位符+格式规范 / User 模式A/B/变体）。"""
    types = payload.question_types or ["choice"]
    type_rules = "\n".join(f"- {_QUESTION_TYPE_RULES.get(t, t)}" for t in types)
    # 用 replace 而非 format：prompt 内含 JSON 示例的 {…} 字面大括号，format 会误判为占位符
    system = _GENERATE_SYSTEM_PROMPT.replace("{type_rules}", type_rules)

    parts = [
        f"知识点：{', '.join(payload.knowledge_points)}",
        f"难度：{payload.difficulty}",
        f"数量：{payload.quantity}",
        f"题型：{', '.join(types)}",
    ]
    if payload.exam_type:
        parts.append(f"考试类型：{payload.exam_type}")
    if payload.barrier_type:
        parts.append(f"针对障碍：{payload.barrier_type}")
    if blueprint:
        parts.append(
            "请基于以下蓝本题生成变式题（数值变体/物质替换/选项重组/题干重写/难度调整），"
            "知识点与难度保持与蓝本一致，但内容必须显著变化，严禁原样复制或仅替换数字："
        )
        parts.append(f"蓝本题内容：{blueprint['content']}")
        parts.append(f"蓝本题答案：{blueprint['answer']}")
    elif samples:
        mode = "A" if len(samples) >= 3 else "B"
        if mode == "A":
            parts.append(
                "以下真题 ≥3 道，请基于以下真题生成变种题，参考其风格与设问角度"
                "（保持知识点一致，改变具体数据/情境）："
            )
        else:
            parts.append("直接按知识点原创出题：")
        parts.append("参考样例：\n" + json.dumps(
            [{"content": s["content"], "answer": s["answer"]} for s in samples], ensure_ascii=False))
    else:
        parts.append("直接按知识点原创出题：")
    if payload.extra_requirements:
        parts.append(f"额外要求/题干约束：{payload.extra_requirements}")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(parts)},
    ]


def _audit_equation_report(content: str) -> dict:
    """四维方程式审核（设计 §3.1.3 step3）→ 卡片可渲染 equation_level 报告。

    审计全部方程式取最严重状态；无方程式记 passed 附说明。化学式 LaTeX 先还原裸箭头/裸下标。
    """
    prepared = content.replace(r"\rightarrow", "→").replace(r"\rightleftharpoons", "⇌")
    prepared = re.sub(r"_\{(\d+)\}", r"\1", prepared)
    equations = extract_equations(prepared)
    if not equations:
        return build_audit_report(None, None, meta={})
    reports = [audit_equation(eq) for eq in equations]
    report = build_audit_report(reports[0], None, meta={})
    worst = report["overall_status"]
    for eq_report in reports[1:]:
        if eq_report.overall_status == "blocked":
            worst = "blocked"
            break
        if eq_report.overall_status == "warning" and worst != "blocked":
            worst = "warning"
    report["equation_level"]["overall_status"] = worst
    report["overall_status"] = worst
    return report


def _generate_batch(db: Session, client: ReviewLLMClient, payload: QuestionGenerateRequest) -> dict:
    """批量生成：RAG/变体蓝本 → LLM 一次生成 → 归一 → 四维审核 → 持久化（含 blocked）。"""
    if payload.variant_qid:
        blueprint = _resolve_blueprint(payload.variant_qid)
        samples: list[dict] = []
        total_available = 1
    else:
        blueprint = None
        samples, total_available = _rag_samples(payload.knowledge_points, payload.quantity)
    try:
        raw = client.complete(_build_generate_messages(payload, samples, blueprint))
        questions = _extract_questions(raw)[:payload.quantity]
    except (ReviewLLMError, ValueError) as exc:
        raise ServerError(
            detail=f"题目生成失败：{exc}", error_code="GENERATION_FAILED",
            suggestion="请稍后重试或检查 LLM 配置",
        )

    items: list[dict] = []
    for q in questions:
        content = normalize_chem_formulas(str(q.get("content") or "")).strip()
        if not content:
            continue
        kps = q.get("knowledge_points") or payload.knowledge_points
        difficulty = _coerce_difficulty(str(q.get("difficulty") or payload.difficulty))
        report = _audit_equation_report(content)
        question = Question(
            content=content,
            options=[normalize_chem_formulas(str(o)) for o in (q.get("options") or [])],
            answer=normalize_chem_formulas(str(q.get("answer") or "")) or "（见解析）",
            analysis=normalize_chem_formulas(str(q.get("analysis") or "")),
            knowledge_points=", ".join(str(k) for k in kps),
            difficulty=Difficulty(difficulty),
            source=QuestionSource.ai,
            audit_status=AuditStatus(report["overall_status"]),
            audit_report=report,
        )
        db.add(question)
        db.flush()
        items.append({
            **question_dict(question),
            "audit_report": report,
            "overall_status": report["overall_status"],
            "generation_failed": False,
        })
    db.commit()
    return {
        "questions": items,
        "generated_count": len(items),
        "total_available": total_available,
    }


def _audit_and_store(
    db: Session,
    question: Question,
    client: ReviewLLMClient,
    meta: dict | None = None,
) -> dict:
    """执行按来源触发的审核，写回 audit_report + audit_status，返回双层报告。

    meta 为 None（重审）时保留题目既有 meta（regeneration_attempts / review_flag 等不丢）；
    显式传入 meta（生成/重生成路径由状态机产出最终 meta）时以其覆盖。
    """
    _, _, report = run_content_audit(
        question.content, question.source.value, client, _question_data(question)
    )
    if meta is None:
        existing = (question.audit_report or {}).get("meta") or {}
        report["meta"] = {**report.get("meta", {}), **existing}
    else:
        report["meta"] = dict(meta)
    question.audit_report = report
    question.audit_status = report["overall_status"]
    db.commit()
    return report


@audit_router.post("/generate")
@require_permission("question", "create")
def generate(
    request: Request,
    payload: QuestionGenerateRequest,
    db: Session = Depends(get_db),
    client: ReviewLLMClient = Depends(get_review_client),
) -> dict:
    """参数驱动批量生成（设计 doc 25 Mode 1）：RAG/变体蓝本 → LLM 生成 → 四维审核 → 入库。"""
    result = _generate_batch(db, client, payload)
    audit_logger.info(
        "questions_generated",
        extra={"event": "questions_generated",
               "generated_count": result["generated_count"],
               "variant": bool(payload.variant_qid)},
    )
    return result


@audit_router.post("/import")
@require_permission("question", "create")
def import_question(
    request: Request,
    payload: ImportRequest,
    db: Session = Depends(get_db),
    client: ReviewLLMClient = Depends(get_review_client),
) -> dict:
    """手动录入 / OCR 单题（设计 Mode 2/3）：manual/ocr 只过方程式级硬闸。"""
    kps = payload.knowledge_points
    if isinstance(kps, list):
        kps = ", ".join(str(k) for k in kps)
    question = Question(
        content=payload.content,
        options=payload.options,
        answer=payload.answer or "（见解析）",
        analysis=payload.analysis,
        knowledge_points=kps,
        difficulty=Difficulty(_coerce_difficulty(payload.difficulty)),
        source=QuestionSource(payload.source),
        audit_status=AuditStatus.passed,
        audit_report={},
    )
    db.add(question)
    db.flush()
    report = _audit_and_store(db, question, client)
    audit_logger.info(
        "question_imported",
        extra={"event": "question_imported", "question_id": question.id,
               "source": payload.source},
    )
    return {
        "question_id": question.id,
        "question": question_dict(question),
        "audit_report": report,
        "overall_status": report["overall_status"],
    }


# 丢反斜杠 LaTeX 命令残片 → 完整命令：OCR 把图片里的 LaTeX 排版当文本读时，反斜杠（及其后首字母）
# 最易丢失。人工兜底（可编辑预览）前先自动补回，只修明确是命令的位置。
_OCR_HARPOON_REPAIR = {
    "rightleftharpoons": r"\rightleftharpoons",
    "ightleftharpoons": r"\rightleftharpoons",
    "leftrightarrow": r"\leftrightarrow",
    "eftrightarrow": r"\leftrightarrow",
    "leftharpoons": r"\leftharpoons",
    "eftharpoons": r"\leftharpoons",
    "rightarrow": r"\rightarrow",
    "ightarrow": r"\rightarrow",
    "leftarrow": r"\leftarrow",
    "eftarrow": r"\leftarrow",
}
_OCR_BRACE_COMMANDS = {
    "ce", "mathrm", "text", "textbf", "mathit", "mathbb",
    "underset", "overset", "frac", "dfrac", "tfrac", "sqrt",
}
_OCR_HARPOON_RE = re.compile(
    r"(?<!\\)(?<![A-Za-z])("
    + "|".join(re.escape(k) for k in sorted(_OCR_HARPOON_REPAIR, key=len, reverse=True))
    + r")(?![A-Za-z])"
)
_OCR_BRACE_RE = re.compile(
    r"(?<!\\)(?<![A-Za-z])("
    + "|".join(re.escape(k) for k in sorted(_OCR_BRACE_COMMANDS, key=len, reverse=True))
    + r")(?=\s*\{)"
)


def _repair_ocr_text(text: str) -> str:
    """修复 OCR 题面文本的 LaTeX 命令残片（丢反斜杠/首字母），其余原文不动。"""
    if not text:
        return text
    text = _OCR_HARPOON_RE.sub(lambda m: _OCR_HARPOON_REPAIR[m.group(1)], text)
    text = _OCR_BRACE_RE.sub(lambda m: "\\" + m.group(1), text)
    return text


# 化学式行自动转 $\ce{...}$（mhchem）：教师无需手动补反斜杠/包分隔符。
# 只处理"纯化学式字符 + 化学箭头 + 含数字"的行；含中文/残留 LaTeX 命令的行保持原样（留给人工）。
_SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_CE_ARROWS = {
    r"\rightleftharpoons": "<=>", r"\leftharpoons": "<=>",
    r"\leftrightarrow": "<->", r"\rightleftarrows": "<=>",
    r"\rightarrow": "->", r"\leftarrow": "<-",
    r"\Longleftrightarrow": "<=>", r"\Longrightarrow": "=>", r"\Longleftarrow": "<=",
    "⇌": "<=>", "⇋": "<=>", "↔": "<->", "→": "->", "⟶": "->", "←": "<-", "⟵": "<-",
    "->": "->", "<=>": "<=>", "<->": "<->", "=>": "=>",
}
_CE_ARROW_RE = re.compile("|".join(re.escape(k) for k in _CE_ARROWS))
_CE_CHARSET_RE = re.compile(r"^[A-Za-z0-9()\[\]+=\-·.<>.]+$")
_CE_TRIGGER_RE = re.compile(r"[0-9]")  # 含数字（系数/下标），排除英文短句


def _ocr_text_to_katex(text: str) -> str:
    """把纯化学式行自动包装为 $\\ce{...}$；已是 $..$ 包裹或非化学式的行保持原样。"""
    if not text:
        return text
    out: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or (s.startswith("$") and s.endswith("$")):
            out.append(line)
            continue
        t = s.translate(_SUBSCRIPT_MAP)
        if not (_CE_ARROW_RE.search(t) and _CE_TRIGGER_RE.search(t)):
            out.append(line)
            continue
        t = _CE_ARROW_RE.sub(lambda m: _CE_ARROWS[m.group(0)], t)
        if _CE_CHARSET_RE.match(t):
            out.append("$\\ce{" + t + "}$")
        else:
            out.append(line)
    return "\n".join(out)


@audit_router.post("/ocr-recognize")
async def ocr_recognize(
    request: Request,
    file: UploadFile = File(...),
) -> dict:
    """题目图片识别（设计 doc 25 §3.3 Mode 3 务实版）：上传→识别→返回题面文本，供可编辑预览。

    复用批改线识别链 extract_document（图片→百度→VLM / PDF→MinerU→百度→VLM）。
    只返回原始识别文本，结构化/入库走 /import（source=ocr 只过方程式级硬闸）。
    识别是一次性操作，落盘临时文件用后即删。
    """
    # async 端点不可套 sync 的 require_permission（wrapper 会返回协程对象），
    # 与 agent/mcp 异步端点同款做法：内联等价检查（401/403 语义一致）。
    permission_checker.check_from_request(request, "question", "create")
    path = save_upload(file, settings.ocr_upload_dir)
    try:
        result = await extract_document(OCRDocument(path=path))
    finally:
        Path(path).unlink(missing_ok=True)
    audit_logger.info(
        "ocr_recognized",
        extra={"event": "ocr_recognized", "provider": result.provider,
               "partial": result.partial, "chars": len(result.text)},
    )
    return {
        "text": _ocr_text_to_katex(_repair_ocr_text(result.text)),
        "provider": result.provider,
        "partial": result.partial,
        "degraded": result.degraded,
        "error": result.error or "",
    }


@audit_router.post("/audit")
@require_permission("question", "create")
def reaudit(
    request: Request,
    payload: AuditRequest,
    db: Session = Depends(get_db),
    client: ReviewLLMClient = Depends(get_review_client),
) -> dict:
    """对已存储题目重新审核，返回双层报告。"""
    question = db.get(Question, payload.question_id)
    if question is None:
        raise NotFoundError()
    report = _audit_and_store(db, question, client)
    return {
        "question_id": question.id,
        "audit_report": report,
        "overall_status": report["overall_status"],
    }


@audit_router.post("/{question_id}/approve")
@require_permission("question", "create")
def approve(
    request: Request,
    question_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """教师批准：passed 入库 / warning 放行带复核标记；blocked 不可批准。"""
    question = db.get(Question, question_id)
    if question is None:
        raise NotFoundError()
    current_status = question.audit_status.value if question.audit_status else "blocked"
    state = AuditState(
        overall_status=current_status,
        regeneration_attempts=(question.audit_report or {}).get("meta", {}).get(
            "regeneration_attempts", 0
        ),
    )
    try:
        next_state = apply_action(state, "approve")
    except ValueError:
        raise HTTPException(status_code=400, detail="blocked 题目不可批准，应自动重生成")

    report = dict(question.audit_report or {})
    meta = dict(report.get("meta", {}))
    meta["status"] = next_state.status  # "approved" = 已入库
    meta["review_flag"] = next_state.review_flag
    report["meta"] = meta
    question.audit_report = report
    db.commit()
    audit_logger.info(
        "question_approved",
        extra={"event": "question_approved", "question_id": question.id,
               "overall": current_status, "review_flag": next_state.review_flag},
    )
    # 保存入库后增量同步向量索引（vector-retrieval spec：新题入库触发索引同步）
    try:
        sync_question_to_vector(question)
    except Exception:  # noqa: BLE001 —— 向量同步失败不阻断批准
        logging.getLogger(__name__).warning("vector sync failed for question %s", question.id)
    return {
        "question_id": question.id,
        "status": "approved",
        "review_flag": next_state.review_flag,
    }


@audit_router.get("/export/{record_id}")
@require_permission("question", "update")
def export_exam(
    request: Request,
    record_id: int,
    format: str = Query(default="docx"),
    with_answers: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> Response:
    """导出试卷（设计 §10.1）：GET /api/question/export/{record_id}?format=docx|pdf&with_answers=true。"""
    exam = db.get(ExamRecord, record_id)
    if exam is None:
        raise NotFoundError()
    questions = [question_dict(q) for q in db.query(Question).filter_by(record_id=record_id).all()]
    filename = f"exam-{record_id}-{'teacher' if with_answers else 'student'}"
    if format == "docx":
        buf = export_paper_docx(questions, title=exam.name, with_answers=with_answers)
        return StreamingResponse(
            io.BytesIO(buf),
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            headers={"Content-Disposition": f'attachment; filename="{filename}.docx"'},
        )
    if format == "pdf":
        pdf = render_paper_pdf(questions, title=exam.name, with_answers=with_answers)
        if pdf is None:
            raise HTTPException(status_code=500, detail="PDF 转换失败")
        return StreamingResponse(
            io.BytesIO(pdf),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
        )
    raise HTTPException(status_code=400, detail="不支持的导出格式，仅支持 docx / pdf")


@audit_router.post("/{question_id}/regenerate")
@require_permission("question", "create")
def regenerate(
    request: Request,
    question_id: int,
    payload: RegenerateRequest,
    db: Session = Depends(get_db),
    client: ReviewLLMClient = Depends(get_review_client),
) -> dict:
    """教师触发重生成：更新题目内容并重新执行两层审核（仅 warning / blocked 可重生成）。"""
    question = db.get(Question, question_id)
    if question is None:
        raise NotFoundError()
    if question.audit_status == AuditStatus.passed:
        raise HTTPException(status_code=400, detail="passed 题目无需重生成")
    if payload.content is not None:
        question.content = payload.content
    if payload.answer is not None:
        question.answer = payload.answer
    if payload.analysis is not None:
        question.analysis = payload.analysis
    if payload.knowledge_points is not None:
        question.knowledge_points = payload.knowledge_points

    report = _audit_and_store(db, question, client)
    meta = dict(report.get("meta", {}))
    meta["regeneration_attempts"] = meta.get("regeneration_attempts", 0) + 1
    # 手动重生成通过后清除自动生成失败的标记：已修复题目不应继续显示"出题失败"
    if report["overall_status"] in ("passed", "warning"):
        meta["generation_failed"] = False
    report["meta"] = meta
    question.audit_report = report
    db.commit()
    return {
        "question_id": question.id,
        "audit_report": report,
        "overall_status": report["overall_status"],
    }
