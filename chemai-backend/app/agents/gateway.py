"""Gateway 意图分类器（doc 30 六 / spec Gateway 意图分类 / design D2 / 11.9）。

双路径架构：LLM 语义分类优先 → 失败降级关键词兜底 → 合并去重输出。
- 分类结果 type：chat（进入 ReAct 循环）/ navigate（快捷路径，跳过 Agent 引擎）。
- 图片/拍照/OCR/识别/上传 → 视觉 Provider（MiMo 主位吸收能力路由，D2）。
- navigate 快捷路径：依次推送 navigate（page+params）、done 与结束帧。

延迟考量（11.9）：维持 LLM 优先 + 记已知延迟；Evals 首帧 P95 验证，超目标再启用关键词前置。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)

# doc 30 §6.2 页面标识
PAGE_EXAM = "exam-v2"         # 出题工作台
PAGE_STUDENTS = "students"    # 学生管理
PAGE_DIAGNOSIS = "diagnosis"  # 诊断页面
PAGE_TEACHER = "teacher"      # 教师首页
PAGE_PARENT = "parent"        # 家长端

# doc 30 §6.2：视觉/图片消息关键字（走视觉 Provider）
IMAGE_KEYWORDS = ("图片", "照片", "图像", "拍照", "OCR", "识别", "上传", "扫描", "截图")

CLASSIFY_SYSTEM_PROMPT = """你是 ChemAI 的意图分类器。判断用户消息属于「需要调用工具」（chat）还是「纯页面跳转」（navigate）。

规则：
- 不确定时一律 chat。
- 只有明确表达"打开某页面 / 跳转到某页面 / 去某页"才返回 navigate，并给出目标页面 page。
- 页面标识：exam-v2（出题工作台）、students（学生管理）、diagnosis（诊断页面）、teacher（教师首页）、parent（家长端）。
- 若为 chat，从下方可用工具中推荐最多 3 个最匹配的工具名。

仅返回 JSON，不要任何其他文字：{{"type": "chat" 或 "navigate", "tools": ["工具名"], "page": "页面标识或空"}}

可用工具：
{tools}
"""

# 关键词兜底组（doc 30 §6.3）：(关键字元组, type, tools, page)
KEYWORD_RULES: list[tuple[tuple[str, ...], str, list[str], Optional[str]]] = [
    # --- navigate：纯页面跳转 ---
    (("打开考试工作台", "出题工作台", "打开出题", "去出题", "出题页面"), "navigate", [], PAGE_EXAM),
    (("打开学生管理", "学生管理页", "打开学生", "学生页面"), "navigate", [], PAGE_STUDENTS),
    (("打开诊断", "诊断页面", "诊断页", "打开学情"), "navigate", [], PAGE_DIAGNOSIS),
    (("去首页", "回到首页", "返回首页", "教师首页"), "navigate", [], PAGE_TEACHER),
    (("打开家长端", "家长页面"), "navigate", [], PAGE_PARENT),
    # --- chat：工具推荐 ---
    (("出", "题", "生成题目", "编题"), "chat", ["generate_questions", "show_exam_workbench", "search_exam_bank"], None),
    (("班", "学生", "名单"), "chat", ["show_students", "diagnose_barrier"], None),
    (("诊断", "学情", "薄弱", "障碍", "分析一下"), "chat", ["diagnose_barrier"], None),
    (("周报", "学习报告", "周报告", "学情报告", "表现"), "chat", ["weekly_report"], None),
    (("网上", "上网", "搜一搜", "搜索一下", "查一下", "最新"), "chat", ["web_search"], None),
    (("题库", "真题", "找题"), "chat", ["search_exam_bank", "list_banks"], None),
    (("保存", "存到", "入库", "存题库"), "chat", ["save_to_bank"], None),
    (("删除", "删掉", "移除题库"), "chat", ["delete_bank"], None),
    (("练习", "布置", "作业", "训练"), "chat", ["assign_adaptive_practice"], None),
    (("学习计划", "学习方案", "计划"), "chat", ["generate_learning_plan", "send_learning_plan"], None),
    (("是什么", "什么是", "怎么做", "怎么理解", "原理", "为什么", "解释"), "chat", ["chemistry_tutor", "web_search"], None),
    (("介绍", "总结", "讲解", "科普"), "chat", ["web_search"], None),
]


@dataclass
class IntentResult:
    """分类结果：type（chat/navigate）+ 最多 3 个建议工具 + 可选 page。"""
    type: str = "chat"                 # chat | navigate
    tools: list[str] = field(default_factory=list)
    page: Optional[str] = None
    source: str = "keyword"            # llm | keyword


def _is_image_message(message: str) -> bool:
    return any(k in message for k in IMAGE_KEYWORDS)


def keyword_classify(message: str) -> IntentResult:
    """关键词兜底（doc 30 §6.3）：命中第一组规则即返回；未命中默认 chat。"""
    for keywords, kind, tools, page in KEYWORD_RULES:
        if any(k in message for k in keywords):
            return IntentResult(type=kind, tools=list(tools), page=page, source="keyword")
    return IntentResult(type="chat", tools=[], source="keyword")


def _parse_intent(raw: Any) -> Optional[IntentResult]:
    """解析 LLM 返回的 JSON；格式异常返回 None（调用方降级关键词）。"""
    if isinstance(raw, dict):  # 测试注入已解析对象
        obj = raw
    else:
        text = (raw or "").strip()
        try:
            obj = json.loads(text)
        except (TypeError, ValueError):
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end == -1:
                return None
            try:
                obj = json.loads(text[start:end + 1])
            except (TypeError, ValueError):
                return None
    if not isinstance(obj, dict):
        return None
    kind = obj.get("type")
    if kind not in ("chat", "navigate"):
        return None
    tools = obj.get("tools") or []
    tools = [t for t in tools if isinstance(t, str)][:3]
    page = obj.get("page") or None
    return IntentResult(type=kind, tools=tools, page=page, source="llm")


def _merge(llm: IntentResult, kw: IntentResult) -> IntentResult:
    """LLM 结果优先：navigate 无 page 补关键词 page；chat 无工具补关键词工具（去重）。"""
    if llm.type == "navigate":
        return IntentResult(type="navigate", tools=llm.tools, page=llm.page or kw.page, source="llm")
    seen, tools = set(), []
    for t in list(llm.tools) + list(kw.tools):
        if t and t not in seen:
            seen.add(t)
            tools.append(t)
        if len(tools) >= 3:
            break
    return IntentResult(type="chat", tools=tools, page=llm.page or kw.page, source="llm")


async def _llm_classify(message: str, llm, image_hint: bool) -> Optional[IntentResult]:
    """LLM 语义分类（doc 30 §6.2）。图片消息先走视觉 Provider（MiMo），失败回退链。"""
    from app.agents.factories.model_factory import ProviderError
    from app.agents.tools.tool_meta import TOOL_META

    tool_list = "\n".join(f"- {name}: {meta.title}" for name, meta in TOOL_META.items())
    messages = [
        {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT.format(tools=tool_list)},
        {"role": "user", "content": message},
    ]
    raw = None
    if image_hint:
        try:
            raw = await llm.acomplete("mimo", messages)  # 视觉 Provider
        except ProviderError:
            raw = None
    if raw is None:
        try:
            raw = await llm.acomplete_chain(messages)
        except Exception:  # noqa: BLE001 —— Provider 全败降级关键词
            logger.warning("[gateway] LLM 分类失败，降级关键词兜底")
            return None
    return _parse_intent(raw)


async def classify_intent(
    message: str, llm=None, image_hint: Optional[bool] = None
) -> IntentResult:
    """入口：LLM 优先 + 关键词兜底 + 合并（doc 30 §6.1）。llm 为空直接走关键词。"""
    kw = keyword_classify(message)
    if llm is None:
        return kw
    hint = _is_image_message(message) if image_hint is None else image_hint
    llm_result = await _llm_classify(message, llm, hint)
    if llm_result is None:
        return kw
    return _merge(llm_result, kw)


async def navigate_shortcut(
    page: str, params: Optional[dict] = None
) -> AsyncIterator[tuple[str, dict]]:
    """navigate 快捷路径（doc 30 §6.4）：跳过 Agent 引擎，直接推 navigate → done → 结束帧（流终止）。"""
    yield "navigate", {"type": "navigate", "page": page, "params": params or {}}
    yield "done", {"type": "done"}
