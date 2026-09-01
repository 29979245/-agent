"""工具元数据注册表（doc 30 §3.1 / design D7/D8）。

以工具名为键注册每个工具的可用 Persona 角色与每轮最大调用次数 call_limit。
编译时完整性校验：每个注册工具都有元数据、每条元数据对应已注册工具（doc 30 §3.1）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

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
    token_rate: Optional[float] = None  # L2 Token Bucket 每秒补充令牌数（None=不启用桶）
    token_capacity: Optional[int] = None  # L2 桶容量（None=默认 rate*2）
    max_concurrent: Optional[int] = None  # L2 并发在途上限（None=沿用全局默认 1）


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
        "搜索本地真题库。适用：用户要求查找/搜索题目。三级搜索：本地关键词→向量召回→联网补齐，"
        "支持 source/region/knowledge_point 过滤；向量命中相似度 ≥0.6 才入列；本地结果不足 3 条时自动标记 AI 补充；"
        "命中含图题目会发 exam_images 事件。NOT for：直接生成新题目（用 generate_questions）。"
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
    max_concurrent=2,  # L2 并发在途上限（TOOL_META 可配；其余工具沿用默认 1）
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
        "按知识点/难度/数量生成题目：RAG 检索→生成→化学式标准化→四维审核（配平/条件/产物/结构；"
        "仅配平失败阻断，条件/产物/结构问题软标记 warning 不阻断），"
        "题目带陷阱提示 trap_hint 与 RAG 元数据。适用：用户要求生成/编写题目。"
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
    description="为班级学生生成个性化 ZPD 练习预览（不落库）。确认后由前端调用 API 持久化（doc 28 §六）。",
    personas=("teacher",),
    call_limit=1,
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

# ---------- 辅导（9 工具，doc 30 §3.4） ----------
register_tool(ToolMeta(
    name="ionic_equation_tutor",
    title="离子方程式辅导",
    description="苏格拉底四步法辅导离子方程式：判断可拆物质→写成离子→删不变离子→检查守恒。适用：学生问离子方程式的书写/配平。NOT for：实验模拟（用 simulate_experiment）。",
    personas=("student",),
    call_limit=5,
    icon="⚗️",
))
register_tool(ToolMeta(
    name="stoichiometry_tutor",
    title="化学计量辅导",
    description="苏格拉底四步法辅导化学计量计算：提取已知量→选公式→列关系式→分步计算。适用：物质的量/浓度/产率计算辅导。",
    personas=("student",),
    call_limit=5,
    icon="⚖️",
))
register_tool(ToolMeta(
    name="redox_tutor",
    title="氧化还原辅导",
    description="苏格拉底四步法辅导氧化还原：标化合价→找升降→电子守恒配平。适用：氧化还原判断与配平辅导。",
    personas=("student",),
    call_limit=5,
    icon="🔁",
))
register_tool(ToolMeta(
    name="equilibrium_tutor",
    title="化学平衡辅导",
    description="苏格拉底四步法辅导化学平衡：分析平衡体系→勒夏特列原理→三段式计算。适用：平衡移动/转化率/三段式计算辅导。",
    personas=("student",),
    call_limit=5,
    icon="⚖️",
))
register_tool(ToolMeta(
    name="periodic_law_tutor",
    title="周期律辅导",
    description="苏格拉底四步法辅导周期律：位置→结构→性质推断。适用：元素周期表位置/结构/性质推断辅导。",
    personas=("student",),
    call_limit=5,
    icon="🔬",
))
register_tool(ToolMeta(
    name="organic_tutor",
    title="有机推断辅导",
    description="苏格拉底四步法辅导有机推断：逆合成分析+官能团转化。适用：有机物推断/官能团转化辅导。",
    personas=("student",),
    call_limit=5,
    icon="🧪",
))
register_tool(ToolMeta(
    name="chemistry_tutor",
    title="通用辅导",
    description="通用化学辅导：teacher 角色输出 800 字教研分析（考点/策略/误区），student/tutor 输出 500 字苏格拉底引导教学。适用：任意化学问题的讲解/辅导。",
    personas=PERSONAS,
    call_limit=3,
    icon="📚",
))
register_tool(ToolMeta(
    name="simulate_experiment",
    title="模拟实验",
    description="LLM 生成实验报告：目的/仪器/步骤/现象/方程式/原理/安全提醒/考点。适用：用户询问某个实验怎么做/现象/原理。",
    personas=("student", "tutor"),
    call_limit=2,
    icon="🧫",
))
register_tool(ToolMeta(
    name="balance_equation",
    title="配平方程式",
    description="确定性算法配平化学方程式并执行四维审核，返回两侧元素原子计数。适用：用户要求配平/验证方程式。不依赖 LLM 配平，结果确定。",
    personas=("tutor", "teacher"),
    call_limit=3,
    icon="🔄",
))

# ---------- OCR 批改（3 工具，doc 30 §3.5） ----------
register_tool(ToolMeta(
    name="query_ocr_progress",
    title="查询批改进度",
    description="按批次聚合 OCR 任务进度（完成/失败/等待百分比 + 每张状态）。适用：用户询问某批次答题卡识别进度。",
    personas=("teacher",),
    call_limit=3,
    icon="⏱️",
))
register_tool(ToolMeta(
    name="grade_answer_sheets",
    title="批量批改",
    description="对已完成 OCR 识别的答题卡批量执行 LLM 批改，只计算不落库，返回批改汇总与逐题判定。适用：用户要求批改某批次。审批类写操作。",
    personas=("teacher",),
    call_limit=2,
    approval=True,
    icon="📝",
))
register_tool(ToolMeta(
    name="save_grading_results",
    title="保存批改结果",
    description="逐学生校验学号后写入作答记录并自动触发障碍诊断，返回保存数量与诊断触发确认。适用：批改确认后保存。审批类写操作。",
    personas=("teacher",),
    call_limit=2,
    approval=True,
    icon="💾",
))

# ---------- 记忆（2 工具，doc 30 §3.6） ----------
register_tool(ToolMeta(
    name="memory_student_get",
    title="学生记忆",
    description="读取学生诊断历史（最近 5 条）与当前学习计划。适用：用户询问该学生的历史诊断/学习计划；学生仅可读自身记忆。",
    personas=PERSONAS,
    call_limit=1,
    icon="🧠",
))
register_tool(ToolMeta(
    name="memory_teacher_get",
    title="教师偏好",
    description="读取教师偏好设置（教学风格/难度偏好/班级配置）。适用：教师询问自己的偏好配置。仅 teacher 可调用。",
    personas=("teacher",),
    call_limit=1,
    icon="⚙️",
))

# ---------- 家长报告（2 工具，doc 30 §3.7） ----------
register_tool(ToolMeta(
    name="generate_parent_report",
    title="生成家长报告",
    description="聚合练习/诊断/知识点数据生成家长可读周报预览，返回需确认标记，不发送。适用：教师要求为某学生生成家长报告预览。",
    personas=("teacher",),
    call_limit=5,
    icon="📋",
))
register_tool(ToolMeta(
    name="send_report_to_parent",
    title="发送家长报告",
    description="推送周报到已绑定家长的通知列表，返回发送确认与已通知数。审批类写操作（外部家长可见），需教师确认。",
    personas=("teacher",),
    call_limit=3,
    approval=True,
    icon="✉️",
))

# ---------- 浏览器（5 工具，doc 30 §3.8，全角色可用） ----------
register_tool(ToolMeta(
    name="browse_navigate",
    title="打开网页",
    description="打开 URL 等待加载完成，返回页面标题与正文文本（上限 8000 字）。适用：用户要求访问/打开某个网页查看内容。",
    personas=PERSONAS,
    call_limit=3,
    icon="🌐",
))
register_tool(ToolMeta(
    name="browse_read",
    title="读取网页元素",
    description="按元素选择器提取页面元素的文本内容（上限 8000 字符）。适用：导航后读取页面特定区域。",
    personas=PERSONAS,
    call_limit=3,
    icon="📖",
))
register_tool(ToolMeta(
    name="browse_click",
    title="点击网页元素",
    description="点击页面元素并等待 0.5s，返回跳转前后 URL。适用：需要点击按钮/链接继续操作。",
    personas=PERSONAS,
    call_limit=3,
    icon="🖱️",
))
register_tool(ToolMeta(
    name="browse_input",
    title="网页输入文本",
    description="清空输入框并填入文本。适用：需要在页面表单/搜索框输入内容。",
    personas=PERSONAS,
    call_limit=3,
    icon="⌨️",
))
register_tool(ToolMeta(
    name="browse_screenshot",
    title="网页截图",
    description="截取指定区域（缺省整页）的 PNG 截图并 Base64 编码返回。适用：需要查看页面视觉效果。",
    personas=PERSONAS,
    call_limit=3,
    icon="📸",
))

# ---------- 学生自助（3 工具，doc 30 §3 学生端） ----------
register_tool(ToolMeta(
    name="show_my_wrong_questions",
    title="我的错题",
    description="列出当前登录学生的错题（按最近作答倒序、累计答错次数，已掌握移除）。适用：学生询问自己的错题/错题本。NOT for：非本人学生（用教师侧错题能力）。",
    personas=("student",),
    call_limit=2,
    icon="📕",
))
register_tool(ToolMeta(
    name="show_my_review_tasks",
    title="我的复习任务",
    description="列出当前登录学生到期的间隔复习任务（艾宾浩斯，pending/overdue 按到期升序）。适用：学生询问复习中心/待复习题目。NOT for：非本人学生。",
    personas=("student",),
    call_limit=2,
    icon="🔁",
))
register_tool(ToolMeta(
    name="show_my_report",
    title="我的学情报告",
    description="返回当前登录学生的学情报告（完成题数/正确率/连续打卡/知识点掌握度/当周报告/学习计划）。适用：学生询问自己的学习情况/报告/掌握度。NOT for：非本人学生。",
    personas=("student",),
    call_limit=2,
    icon="📊",
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
