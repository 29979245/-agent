"""工具元数据注册表（doc 30 §3.1 / design D7/D8）。

以工具名为键注册每个工具的可用 Persona 角色与每轮最大调用次数 call_limit。
编译时完整性校验：每个注册工具都有元数据、每条元数据对应已注册工具（doc 30 §3.1）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

PERSONAS = ("teacher", "student", "tutor", "parent")


@dataclass(frozen=True)
class ToolMeta:
    name: str
    title: str  # 前端工具卡片显示名
    description: str  # LLM 面向的 docstring：何时用/会发生什么/下一步/NOT for
    personas: tuple[str, ...]
    call_limit: int
    approval: bool = False
    icon: str = ""


TOOL_META: dict[str, ToolMeta] = {}


def register_tool(meta: ToolMeta) -> ToolMeta:
    """注册工具元数据（模块加载时执行）。"""
    TOOL_META[meta.name] = meta
    return meta


# ---------- 出题与题库（7 工具，doc 30 §3.2） ----------
register_tool(ToolMeta(
    name="search_exam_bank",
    title="搜索真题",
    description=(
        "搜索本地真题库。适用：用户要求查找/搜索题目。三级搜索：本地关键词→向量召回→联网补齐；"
        "本地结果不足 3 条时自动标记 AI 补充。NOT for：直接生成新题目（用 generate_questions）。"
    ),
    personas=("teacher", "tutor"),
    call_limit=3,
    icon="🔍",
))
register_tool(ToolMeta(
    name="web_search",
    title="联网搜索",
    description="多路联网搜索并返回 ≤400 字摘要。适用：查询题库外的知识、最新资讯、概念解释。",
    personas=PERSONAS,
    call_limit=2,
    icon="🌐",
))
register_tool(ToolMeta(
    name="show_exam_workbench",
    title="打开出题面板",
    description="在对话中内联渲染出题工作台面板，用户可在面板配置参数生成题目。适用：用户要求出题/配置出题参数。",
    personas=("teacher", "tutor"),
    call_limit=3,
    icon="🛠️",
))
register_tool(ToolMeta(
    name="generate_questions",
    title="AI 出题",
    description=(
        "按知识点/难度/数量生成题目：RAG 检索→生成→化学式标准化→四维审核。适用：用户要求生成/编写题目。"
        "NOT for：仅搜索已有题目（用 search_exam_bank）。"
    ),
    personas=("teacher", "tutor"),
    call_limit=5,
    icon="⚡",
))
register_tool(ToolMeta(
    name="save_to_bank",
    title="保存到题库",
    description="创建题库文件夹、逐题入库并同步向量索引。适用：用户要求把题目保存进题库。",
    personas=("teacher", "tutor"),
    call_limit=1,
    icon="💾",
))
register_tool(ToolMeta(
    name="list_banks",
    title="查看题库列表",
    description="列出全部题库文件夹名称与题目数。适用：用户询问有哪些题库。",
    personas=("teacher", "tutor"),
    call_limit=1,
    icon="📂",
))
register_tool(ToolMeta(
    name="delete_bank",
    title="删除题库",
    description="删除题库文件夹及题目关联（题目实体保留）。破坏性操作，需教师审批确认。",
    personas=("teacher", "tutor"),
    call_limit=1,
    approval=True,
    icon="🗑️",
))

# ---------- 诊断与学生（7 工具，doc 30 §3.3） ----------
register_tool(ToolMeta(
    name="diagnose_barrier",
    title="诊断障碍",
    description=(
        "个体或班级两级障碍诊断：个体返回三维障碍分布与主导类型，班级返回统计分布。"
        "适用：用户询问学生/班级的学习障碍、薄弱点、学情。支持姓名或 ID 智能解析。"
    ),
    personas=("teacher", "parent"),
    call_limit=2,
    icon="🩺",
))
register_tool(ToolMeta(
    name="show_diagnosis",
    title="展示诊断面板",
    description="在对话中内联渲染诊断图表面板。适用：诊断完成后展示可视化图表。",
    personas=("teacher",),
    call_limit=1,
    icon="📊",
))
register_tool(ToolMeta(
    name="show_students",
    title="展示学生列表",
    description="三模式学生列表：无班级列出班级；有班级显示学生卡片；有过滤按障碍筛选。",
    personas=("teacher",),
    call_limit=1,
    icon="👥",
))
register_tool(ToolMeta(
    name="weekly_report",
    title="生成周报",
    description="生成 ≤200 字自然语言周报，通俗不制造焦虑。适用：用户要求周报/学习报告。",
    personas=("teacher", "parent"),
    call_limit=2,
    icon="📰",
))
register_tool(ToolMeta(
    name="assign_adaptive_practice",
    title="布置自适应练习",
    description="为班级学生生成个性化 ZPD 练习并布置。破坏性操作，需教师审批确认。",
    personas=("teacher",),
    call_limit=1,
    approval=True,
    icon="📝",
))
register_tool(ToolMeta(
    name="generate_learning_plan",
    title="生成学习计划",
    description="跳转学生管理页并触发学习方案生成。适用：用户要求为某学生生成学习计划。",
    personas=("teacher",),
    call_limit=5,
    icon="🗓️",
))
register_tool(ToolMeta(
    name="send_learning_plan",
    title="发送学习计划",
    description="持久化学习计划并通知学生。适用：学习计划确认后发送。",
    personas=("teacher",),
    call_limit=2,
    icon="✈️",
))


def integrity_check() -> list[str]:
    """编译时完整性校验（doc 30 §3.1 / doc 30 §十七）。返回问题列表，空=通过。"""
    problems: list[str] = []
    for name, meta in TOOL_META.items():
        if meta.name != name:
            problems.append(f"元数据键与 name 不一致: {name} != {meta.name}")
        for p in meta.personas:
            if p not in PERSONAS:
                problems.append(f"工具 {name} 含未识别的 Persona: {p}")
    return problems


def tools_for_persona(persona: str) -> list[str]:
    """Persona 可用工具：TOOL_META 中注册该 persona 的工具名列表（有序）。"""
    return [name for name, meta in TOOL_META.items() if persona in meta.personas]


def tool_descriptions(names: list[str]) -> str:
    """生成注入 LLM 的工具描述块（name + docstring + NOT for）。"""
    lines = []
    for name in names:
        meta = TOOL_META.get(name)
        if meta:
            lines.append(f"- {meta.name}: {meta.description}")
    return "\n".join(lines)
