"""辅导工具组（doc 30 §3.4，9 工具）——6 专题苏格拉底 + 通用辅导 + 实验模拟 + 配平。

契约要点：
- 6 专题工具由同一工厂生成（doc 30 §8.2），按各自方法分步引导，不直接给答案。
- chemistry_tutor 按角色分流：teacher 800 字教研分析 / student+tutor 500 字引导教学。
- balance_equation 用确定性算法配平（doc 30 §3.4：不依赖 LLM 配平），返回两侧原子计数。
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from app.agents.tools.context import ToolContext, user_role
from app.services.audit.equation import audit_equation, check_balance

# ---------------------------------------------------------------- 参数 Schema

class TopicTutorArgs(BaseModel):
    equation: Optional[str] = Field(default=None, description="方程式（可选）")
    question: Optional[str] = Field(default=None, description="题目（可选）")
    student_input: Optional[str] = Field(default=None, description="学生输入（可选）")


class ChemistryTutorArgs(BaseModel):
    question: str = Field(..., description="问题")
    student_level: Optional[str] = Field(default=None, description="学生水平（可选）")
    role: Optional[str] = Field(default=None, description="显式角色：teacher/student/tutor（缺省按登录角色）")


class SimulateExperimentArgs(BaseModel):
    experiment: str = Field(..., description="实验名称")


class BalanceEquationArgs(BaseModel):
    equation: str = Field(..., description="化学方程式（如 H2 + O2 → H2O）")


# ---------------------------------------------------------------- 工具实现

_TOPIC_CFG = {
    "ionic_equation_tutor": {
        "title": "离子方程式",
        "method": "判断可拆物质→写成离子→删不变离子→检查守恒",
        "extra": "重点：强弱电解质是否可拆、删去未参与反应的离子、电荷与原子守恒。",
    },
    "stoichiometry_tutor": {
        "title": "化学计量",
        "method": "提取已知量→选公式→列关系式→分步计算",
        "extra": "重点：量纲统一（g/mol/L）、按化学计量比换算、有效数字。",
    },
    "redox_tutor": {
        "title": "氧化还原",
        "method": "标化合价→找升降→电子守恒配平",
        "extra": "重点：化合价升降守恒、氧化剂/还原剂判断、电子得失守恒配平。",
    },
    "equilibrium_tutor": {
        "title": "化学平衡",
        "method": "分析平衡体系→勒夏特列原理→三段式计算",
        "extra": "重点：勒夏特列原理的移动方向判断、三段式（起始/变化/平衡）计算。",
    },
    "periodic_law_tutor": {
        "title": "周期律",
        "method": "位置→结构→性质推断",
        "extra": "重点：由位置推结构、由结构推性质、金属性/非金属性递变规律。",
    },
    "organic_tutor": {
        "title": "有机推断",
        "method": "逆合成分析+官能团转化",
        "extra": "重点：官能团转化关系、条件判断、逆合成思维倒推。",
    },
}

_SOCRATIC_SYSTEM = (
    "你是 ChemAI 的{title}辅导老师。使用苏格拉底式四步法引导学生：{method}。\n"
    "规则：1. 绝对禁止直接给出答案或完整解答，先就「学生哪里卡住」提问，再一步步引导；\n"
    "2. 化学方程式、计算一律 LaTeX 格式（下标用 _{{n}}）；\n"
    "3. 学生答错时引导回忆相关知识点，不否定；4. 每次回复 ≤200 字，聚焦下一步。\n"
    "专题要点：{extra}"
)


def _make_topic_tutor(topic: str):
    cfg = _TOPIC_CFG[topic]

    async def _tutor(ctx: ToolContext, equation=None, question=None, student_input=None) -> dict:
        if ctx.llm is None:
            return {"error": "llm_unavailable", "message": "LLM 未配置，无法辅导", "_guard_error": True}
        system = _SOCRATIC_SYSTEM.format(
            title=cfg["title"], method=cfg["method"], extra=cfg["extra"]
        )
        parts = [f"专题：{cfg['title']}（{cfg['method']}）"]
        if equation:
            parts.append(f"方程式：{equation}")
        if question:
            parts.append(f"题目：{question}")
        if student_input:
            parts.append(f"学生已给出输入：{student_input}")
        if not (equation or question or student_input):
            parts.append("学生尚未提供具体题目，请用一道典型示例开启引导。")
        try:
            raw = await ctx.llm.acomplete_chain([
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(parts)},
            ])
        except Exception as exc:  # noqa: BLE001 —— 上游失败给出可读错误
            return {"error": "llm_failed", "message": f"辅导生成失败：{exc}", "_guard_error": True}
        return {
            "topic": topic,
            "title": cfg["title"],
            "method": cfg["method"],
            "guidance": raw,
        }

    _tutor.__name__ = topic
    return _tutor


async def chemistry_tutor(
    ctx: ToolContext,
    question: str,
    student_level: Optional[str] = None,
    role: Optional[str] = None,
) -> dict:
    """通用辅导：teacher → 800 字教研分析；student/tutor → 500 字引导教学（doc 30 §3.4 工具 21）。"""
    if ctx.llm is None:
        return {"error": "llm_unavailable", "message": "LLM 未配置，无法辅导", "_guard_error": True}
    eff_role = role or user_role(ctx)
    if eff_role == "teacher":
        system = (
            "你是 ChemAI 的化学教研分析助手。就给定问题输出 800 字教研分析："
            "1. 考点分布；2. 教学策略（怎么教）；3. 学生误区与易错点。化学式 LaTeX 格式，结论明确。"
        )
    else:
        system = (
            "你是 ChemAI 的化学助教。就给定问题做 500 字引导教学：苏格拉底式分步引导，"
            "不直接给答案，结尾给出一个引导性问题。化学式 LaTeX 格式。"
        )
    parts = [f"问题：{question}"]
    if student_level:
        parts.append(f"学生水平：{student_level}")
    parts.append(f"角色模式：{'教研分析' if eff_role == 'teacher' else '引导教学'}")
    try:
        raw = await ctx.llm.acomplete_chain([
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(parts)},
        ])
    except Exception as exc:  # noqa: BLE001
        return {"error": "llm_failed", "message": f"辅导生成失败：{exc}", "_guard_error": True}
    return {
        "question": question,
        "mode": "research" if eff_role == "teacher" else "guidance",
        "response": raw,
    }


_EXPERIMENT_SYSTEM = (
    "你是 ChemAI 的实验模拟助手。为给定实验生成实验报告，严格包含以下 8 个部分，每部分用「名称：」开头：\n"
    "目的 / 仪器 / 步骤 / 现象 / 方程式 / 原理 / 安全提醒 / 考点。\n"
    "方程式用 LaTeX（下标 _{n}）。现象要具体可观察，安全提醒必须明确禁忌操作。"
)


async def simulate_experiment(ctx: ToolContext, experiment: str) -> dict:
    """LLM 生成实验报告：目的/仪器/步骤/现象/方程式/原理/安全提醒/考点（doc 30 §3.4 工具 22）。"""
    if ctx.llm is None:
        return {"error": "llm_unavailable", "message": "LLM 未配置，无法模拟实验", "_guard_error": True}
    try:
        raw = await ctx.llm.acomplete_chain([
            {"role": "system", "content": _EXPERIMENT_SYSTEM},
            {"role": "user", "content": f"实验名称：{experiment}"},
        ])
    except Exception as exc:  # noqa: BLE001
        return {"error": "llm_failed", "message": f"实验报告生成失败：{exc}", "_guard_error": True}
    return {"experiment": experiment, "report": raw}


def _normalize_equation(text: str) -> str:
    """LaTeX → 裸式：剥离 $...$、下标 _{n} → n、\rightarrow → →、\rightleftharpoons → ⇌。"""
    text = text.replace("$", "").strip()
    text = re.sub(r"_\{(\d+)\}", r"\1", text)
    text = text.replace(r"\rightarrow", "→").replace(r"\rightleftharpoons", "⇌")
    return text


def balance_equation(ctx: ToolContext, equation: str) -> dict:
    """确定性配平 + 四维审核（doc 30 §3.4 工具 23）。不依赖 LLM 配平。"""
    eq = _normalize_equation(equation)
    try:
        report = audit_equation(eq)
    except Exception as exc:  # noqa: BLE001 —— 解析失败给出可读错误
        return {"error": "equation_parse_failed", "message": f"方程式无法解析：{exc}", "_guard_error": True}
    balance = check_balance(eq)
    return {
        "equation": eq,
        "balanced": balance.status == "passed",
        "balance_status": balance.status,
        "balance_message": balance.message,
        "left_counts": balance.left_elements,
        "right_counts": balance.right_elements,
        "dimensions": {
            "balance": report.balance.status,
            "condition": report.condition.status,
            "product": report.product.status,
            "structure": report.structure.status,
        },
    }


# 6 个专题工具由工厂生成
ionic_equation_tutor = _make_topic_tutor("ionic_equation_tutor")
stoichiometry_tutor = _make_topic_tutor("stoichiometry_tutor")
redox_tutor = _make_topic_tutor("redox_tutor")
equilibrium_tutor = _make_topic_tutor("equilibrium_tutor")
periodic_law_tutor = _make_topic_tutor("periodic_law_tutor")
organic_tutor = _make_topic_tutor("organic_tutor")
