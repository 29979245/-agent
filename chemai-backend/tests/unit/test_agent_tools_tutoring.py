"""辅导组 9 工具单测（tasks 1.2）：6 专题工厂 / 通用辅导分流 / 配平确定性 / 实验模拟。"""
import asyncio

import pytest

from app.agents.tools import tools_tutoring
from app.agents.tools.context import ToolContext

TOPICS = (
    "ionic_equation_tutor",
    "stoichiometry_tutor",
    "redox_tutor",
    "equilibrium_tutor",
    "periodic_law_tutor",
    "organic_tutor",
)


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    def __init__(self, text="引导语：先判断该反应的本质…"):
        self._text = text
        self.last_messages = None

    async def acomplete_chain(self, messages):
        self.last_messages = messages
        return self._text


def _ctx(llm=None, role="student"):
    return ToolContext(user={"user_id": 1, "role": role}, llm=llm)


def test_topic_tutor_socratic_method_per_topic():
    """6 专题由工厂生成，system prompt 含各自方法步骤，不直接给答案。"""
    for topic in TOPICS:
        fn = getattr(tools_tutoring, topic)
        llm = FakeLLM()
        result = run(fn(_ctx(llm), question="Q", student_input="我不会"))
        assert result["topic"] == topic
        assert result["guidance"] == llm._text
        system = llm.last_messages[0]["content"]
        method = tools_tutoring._TOPIC_CFG[topic]["method"]
        assert method in system
        assert "苏格拉底" in system
        assert "绝对禁止直接给出答案" in system


def test_topic_tutor_no_input_uses_example():
    """无方程式/题目/输入时，要求 LLM 用典型示例开启引导。"""
    llm = FakeLLM()
    result = run(tools_tutoring.ionic_equation_tutor(_ctx(llm)))
    user_content = llm.last_messages[1]["content"]
    assert "典型示例" in user_content
    assert result["guidance"]


def test_chemistry_tutor_teacher_research_mode():
    llm = FakeLLM()
    result = run(tools_tutoring.chemistry_tutor(_ctx(llm, role="teacher"), question="如何讲解氧化还原"))
    assert result["mode"] == "research"
    system = llm.last_messages[0]["content"]
    assert "800 字教研分析" in system


def test_chemistry_tutor_student_guidance_mode():
    llm = FakeLLM()
    result = run(tools_tutoring.chemistry_tutor(_ctx(llm, role="student"), question="什么是氧化还原"))
    assert result["mode"] == "guidance"
    system = llm.last_messages[0]["content"]
    assert "500 字引导教学" in system


def test_balance_equation_deterministic():
    """配平确定性零误差：返回两侧原子计数相等，不依赖 LLM。"""
    ctx = _ctx(llm=None)  # 不配 LLM 也必须可配平
    result = tools_tutoring.balance_equation(ctx, "2H2 + O2 → 2H2O")
    assert result["balanced"] is True
    assert result["left_counts"] == {"H": 4, "O": 2}
    assert result["right_counts"] == {"H": 4, "O": 2}
    assert result["balance_status"] == "passed"


def test_balance_equation_unbalanced_flagged():
    ctx = _ctx(llm=None)
    result = tools_tutoring.balance_equation(ctx, "H2 + O2 → H2O")
    assert result["balanced"] is False
    assert result["balance_status"] == "blocked"


def test_balance_equation_latex_normalized():
    """LaTeX 输入（下标 _{n}、\rightarrow）先归一化再配平。"""
    ctx = _ctx(llm=None)
    result = tools_tutoring.balance_equation(ctx, r"2H_{2} + O_{2} \rightarrow 2H_{2}O")
    assert result["balanced"] is True
    assert result["equation"] == "2H2 + O2 → 2H2O"


def test_balance_equation_parse_fail():
    ctx = _ctx(llm=None)
    result = tools_tutoring.balance_equation(ctx, "这不是方程式")
    assert result["error"] == "equation_parse_failed"


def test_simulate_experiment_report():
    llm = FakeLLM("目的：…；仪器：…；步骤：…")
    result = run(tools_tutoring.simulate_experiment(_ctx(llm), "实验室制取氧气"))
    assert result["experiment"] == "实验室制取氧气"
    assert result["report"] == llm._text
    system = llm.last_messages[0]["content"]
    for section in ("目的", "仪器", "步骤", "现象", "方程式", "原理", "安全提醒", "考点"):
        assert section in system


def test_tutoring_llm_unavailable():
    ctx = _ctx(llm=None)
    result = run(tools_tutoring.redox_tutor(ctx, question="Q"))
    assert result["error"] == "llm_unavailable"
