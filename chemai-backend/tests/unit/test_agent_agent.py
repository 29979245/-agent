"""ReAct v2 Agent 工厂 + 对话执行管线单测（tasks 对话闭环）。

覆盖：
- version 门：默认 v2，v1/非法值 → AgentVersionError（端点映射 501）。
- D14 检查点键：thread_key = `{subject}:{thread_id}`；config 带 recursion_limit 12。
- build_chat_agent：create_react_agent 包装 + 系统提示词；v1 抛错。
- 审批挂起注册表：store/get/pop。
- run_agent_chat 闭环：读检查点 → 裁剪 → 工具构建 → ReAct → SSE 适配器。
  - 纯文本回复：phase(text) → done。
  - 工具往返：tool_call → tool_args → executing → tool_result → text → done。
"""
import asyncio
import os
import sqlite3

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolCallChunk,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from app.agents.agent import (
    DEFAULT_VERSION,
    RECURSION_LIMIT,
    AgentVersionError,
    _message_text,
    _orphaned_tool_calls,
    build_chat_agent,
    get_pending_approval,
    list_user_thread_keys,
    make_agent_config,
    pop_pending_approval,
    read_history,
    resolve_version,
    run_agent_chat,
    serialize_history,
    store_pending_approval,
    thread_created_ms,
    thread_key,
)
from app.agents.factories.model_factory import LLMClient
from app.core.permissions import UserContext
from app.db.models import (
    Account,
    Class as ClassModel,
    Grade,
    Parent,
    School,
    Student,
    StudentParentBinding,
)
from app.db.models.enums import AccountRole


# ---------------------------------------------------------------- 假模型

class FakeModel(BaseChatModel):
    """固定文本回复的假模型（同时支持 ainvoke/astream）。"""

    def __init__(self, reply="ok"):
        super().__init__()
        self._reply = reply

    @property
    def _llm_type(self):
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._reply))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        # langchain-core 1.6：_astream 须产出 ChatGenerationChunk（含 .message）
        yield ChatGenerationChunk(message=AIMessageChunk(content=self._reply))

    def bind_tools(self, tools, **kwargs):
        return self


class CapturingModel(BaseChatModel):
    """捕获每次调用传入的 messages（用于验证身份注入等上下文组装）。"""

    reply: str = "ok"
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.reply))

    def bind_tools(self, tools, **kwargs):
        return self


class ScriptedToolModel(BaseChatModel):
    """脚本化假模型：第 1 次调用返回 web_search 工具调用，之后返回文本。"""

    calls: int = 0  # pydantic 字段（BaseChatModel 禁止 setattr 非字段属性）

    @property
    def _llm_type(self):
        return "fake"

    def _next(self):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(content="", tool_calls=[
                {"name": "web_search", "args": {"query": "化学"}, "id": "c1", "type": "tool_call"}
            ])
        return AIMessage(content="已联网查询")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self._next()
        if msg.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                ToolCallChunk(index=0, name="web_search", args='{"query": "化学"}', id="c1")
            ]))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(content="已联网查询"))

    def bind_tools(self, tools, **kwargs):
        return self


def _client(model) -> LLMClient:
    return LLMClient(model_builder=lambda provider: model)


def _cp(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    return AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db"))


# ---------------------------------------------------------------- version 门

def test_resolve_version_default_v2():
    assert resolve_version(None) == "v2"
    assert resolve_version("v2") == "v2"
    assert resolve_version("") == "v2"


def test_resolve_version_v1_raises():
    with pytest.raises(AgentVersionError):
        resolve_version("v1")


def test_resolve_version_unknown_raises():
    with pytest.raises(AgentVersionError):
        resolve_version("v3")


# ---------------------------------------------------------------- D14 检查点键

def test_thread_key_subject_and_thread():
    assert thread_key(5, "abc") == "5:abc"
    assert thread_key("2024001", "t1") == "2024001:t1"


def test_make_agent_config_recursion_limit():
    cfg = make_agent_config("5:abc")
    assert cfg["configurable"]["thread_id"] == "5:abc"
    assert cfg["recursion_limit"] == RECURSION_LIMIT


# ---------------------------------------------------------------- build_chat_agent

def test_build_chat_agent_v2_returns_compiled():
    agent = build_chat_agent(FakeModel(), [], "你是化学老师", version="v2")
    assert hasattr(agent, "ainvoke")
    assert hasattr(agent, "astream_events")


def test_build_chat_agent_v1_raises():
    with pytest.raises(AgentVersionError):
        build_chat_agent(FakeModel(), [], "sys", version="v1")


# ---------------------------------------------------------------- 审批挂起注册表

def test_pending_approval_registry_roundtrip():
    key = thread_key(5, "t1")
    assert get_pending_approval(key) is None
    store_pending_approval(key, {"tool": "assign_adaptive_practice", "args": {"class_id": 1}, "thread_id": "t1"})
    info = get_pending_approval(key)
    assert info["tool"] == "assign_adaptive_practice"
    assert pop_pending_approval(key)["thread_id"] == "t1"
    assert get_pending_approval(key) is None
    assert pop_pending_approval("missing") is None


# ---------------------------------------------------------------- run_agent_chat 闭环

def test_run_agent_chat_text_reply(tmp_path):
    async def _run():
        frames = []
        async for f in run_agent_chat(
            persona="student",
            user=UserContext(user_id=1, role="student"),
            thread_id="t1",
            message="你好",
            llm_client=_client(FakeModel(reply="你好，我是化学助手")),
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    frames = asyncio.run(_run())
    names = [n for n, _ in frames]
    assert names[-1] == "done"
    texts = [p["content"] for n, p in frames if n == "text"]
    assert "你好，我是化学助手" in texts
    # 无工具调用 → 无 tool_call 事件
    assert "tool_call" not in names


def test_run_agent_chat_tool_roundtrip(tmp_path):
    async def _run():
        frames = []
        async for f in run_agent_chat(
            persona="student",
            user=UserContext(user_id=2, role="student"),
            thread_id="t2",
            message="帮我查一下化学知识点",
            llm_client=_client(ScriptedToolModel()),
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    frames = asyncio.run(_run())
    names = [n for n, _ in frames]
    # tool_call(preparing) → tool_args → executing → tool_result → reply(text) → done
    assert names == ["tool_call", "tool_args", "phase", "tool_result", "phase", "text", "done"]
    # tool_result：web_search 未配置 → 优雅降级摘要
    tr = next(p for n, p in frames if n == "tool_result")
    assert tr["success"] is True
    assert tr["tool"] == "web_search"
    assert "联网搜索服务未配置" in tr["result"]["summary"]
    # 最终文本
    texts = [p["content"] for n, p in frames if n == "text"]
    assert texts[-1] == "已联网查询"


def test_run_agent_chat_persona_toolset_bounded(tmp_path):
    """student Persona 仅辅导/搜索/自读记忆：模型无权调用诊断/出题/教师专用工具。"""
    calls = {}

    class TrapModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "fake"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            # 修复后工具以预格式化 OpenAI dict 直传 _astream（不再经 bind_tools）
            tools = kwargs.get("tools") or []
            calls["tools"] = {t["function"]["name"] for t in tools}
            yield AIMessageChunk(content="ok")

    async def _run():
        frames = []
        async for f in run_agent_chat(
            persona="student",
            user=UserContext(user_id=3, role="student"),
            thread_id="t3",
            message="hi",
            llm_client=_client(TrapModel()),
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    asyncio.run(_run())
    bound = calls["tools"]
    assert bound == {"chemistry_tutor", "simulate_experiment", "web_search",
                     "ionic_equation_tutor", "stoichiometry_tutor", "redox_tutor", "equilibrium_tutor",
                     "memory_student_get",
                     "show_my_wrong_questions", "show_my_review_tasks", "show_my_report",
                     "browse_navigate", "browse_read", "browse_click", "browse_input", "browse_screenshot"}
    assert "diagnose_barrier" not in bound
    assert "generate_questions" not in bound
    assert "memory_teacher_get" not in bound


def test_run_agent_chat_injects_student_profile(tmp_path):
    """身份注入（§9.1）：绑定学生档案的 MemorySystem → 模型上下文首条含「学生档案」System。"""
    from app.agents.memory import MemorySystem

    mem = MemorySystem()
    mem.bind_student_profile({"student_id": 1, "name": "小明", "class_id": 5, "class_name": "高一(1)班"})
    model = CapturingModel()

    async def _run():
        frames = []
        async for f in run_agent_chat(
            persona="student",
            user=UserContext(user_id=1, role="student"),
            thread_id="t4",
            message="查看我的错题",
            llm_client=_client(model),
            memory=mem,
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    asyncio.run(_run())
    msgs = model.calls[0]
    system_text = " ".join(m.content for m in msgs if getattr(m, "type", None) == "system")
    assert "学生档案" in system_text
    assert "小明" in system_text
    assert "高一(1)班" in system_text


def test_run_agent_chat_two_turn_tool_then_text_same_thread(tmp_path):
    """回归（对话出错修复）：同线程两轮——首轮工具调用后，次轮不报错且历史不翻倍。

    修复前：次轮重喂整个历史，ToolMessage 被 `_to_plain_messages` 拍平成
    `{role:'tool', content}`（丢 tool_call_id），LangGraph 重建时 KeyError → error 帧；
    且 checkpointer 恢复 + append 双源注入使历史每轮翻倍。
    """
    model = ScriptedToolModel()
    thread = "t9"

    async def _turn(msg):
        frames = []
        async for f in run_agent_chat(
            persona="student",
            user=UserContext(user_id=4, role="student"),
            thread_id=thread,
            message=msg,
            llm_client=_client(model),
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    async def _checkpoint_messages():
        cp = _cp(tmp_path)
        async with cp as saver:
            return await read_history(saver, make_agent_config(thread_key(4, thread)))

    # 首轮：工具往返
    frames1 = asyncio.run(_turn("查看我的错题"))
    names1 = [n for n, _ in frames1]
    assert "tool_call" in names1 and "tool_result" in names1
    assert "error" not in names1
    assert names1[-1] == "done"

    # 次轮：同线程恢复，不得报错
    frames2 = asyncio.run(_turn("什么是氧化还原反应"))
    names2 = [n for n, _ in frames2]
    assert "error" not in names2
    assert names2[-1] == "done"
    texts2 = [p["content"] for n, p in frames2 if n == "text"]
    assert texts2 and texts2[-1] == "已联网查询"

    # 历史不翻倍：两轮恰 2 条 human、1 条 tool 消息（重喂会各增一份）
    msgs = asyncio.run(_checkpoint_messages())
    humans = [m for m in msgs if getattr(m, "type", None) == "human"]
    tool_msgs = [m for m in msgs if getattr(m, "type", None) == "tool"]
    assert len(humans) == 2
    assert humans[0].content == "查看我的错题"
    assert humans[1].content == "什么是氧化还原反应"
    assert len(tool_msgs) == 1


# ---------------------------------------------------------------- 序列化 + 线程枚举（对话跨 tab 持久化）


def _ai_msg(content, tool_calls=()):
    return AIMessage(content=content, tool_calls=list(tool_calls))


def test_message_text_plain_string():
    assert _message_text("你好") == "你好"
    assert _message_text("") == ""


def test_message_text_content_blocks():
    blocks = [{"type": "text", "text": "Na"}, {"type": "image_url", "image_url": "x"}, "Cl"]
    assert _message_text(blocks) == "NaCl"
    assert _message_text([]) == ""
    assert _message_text(None) == ""


def test_serialize_history_filters_roles():
    msgs = [
        SystemMessage(content="你是化学助手"),
        HumanMessage(content="什么是摩尔质量？"),
        _ai_msg("摩尔质量是每摩尔物质的质量。"),
        _ai_msg("", tool_calls=[{"name": "web_search", "args": {"query": "x"}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="{\"result\": []}", tool_call_id="c1"),
    ]
    out = serialize_history(msgs)
    assert out == [
        {"role": "user", "content": "什么是摩尔质量？"},
        {"role": "assistant", "content": "摩尔质量是每摩尔物质的质量。"},
    ]


def test_serialize_history_skips_empty_ai():
    out = serialize_history([_ai_msg("")])
    assert out == []


def test_serialize_history_content_blocks():
    msgs = [HumanMessage(content=[{"type": "text", "text": "配平 "}, {"type": "text", "text": "H2+O2"}]),
            _ai_msg("生成 H2O")]
    assert serialize_history(msgs) == [
        {"role": "user", "content": "配平 H2+O2"},
        {"role": "assistant", "content": "生成 H2O"},
    ]


def test_serialize_history_dict_messages():
    msgs = [{"type": "human", "content": "你好"},
            {"type": "ai", "content": "你好，我是化学助手"},
            {"type": "ai", "content": ""},
            {"type": "tool", "content": "{}"}]
    assert serialize_history(msgs) == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，我是化学助手"},
    ]


def test_thread_created_ms_conv_format():
    assert thread_created_ms("conv_1788181384586_874140") == 1788181384586
    assert isinstance(thread_created_ms("conv_1788181384586_874140"), int)


def test_thread_created_ms_other_formats_none():
    assert thread_created_ms("default") is None
    assert thread_created_ms("conv_abc_1") is None
    assert thread_created_ms("") is None


def _checkpoint_db(tmp_path, rows):
    """构造含 checkpoints 表的临时库。rows: (thread_id, checkpoint_id) 列表。"""
    con = sqlite3.connect(str(tmp_path / "cp.db"))
    try:
        con.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT)")
        con.executemany("INSERT INTO checkpoints (thread_id, checkpoint_id) VALUES (?, ?)", rows)
        con.commit()
    finally:
        con.close()


def test_list_user_thread_keys_filters_and_orders(tmp_path):
    # 同一用户多线程 + 他人线程 + 前缀相似用户（1 与 10）混排，验证 `{user_id}:` 精确前缀
    _checkpoint_db(tmp_path, [
        ("1:old", "b"),      # 用户 1，较旧
        ("1:new", "z"),      # 用户 1，最近
        ("2:other", "a"),    # 用户 2，应排除
        ("10:decoy", "y"),   # 用户 10，前缀以 "1" 开头但不能误匹配 "1:"
    ])
    keys = list_user_thread_keys(1, db_path=str(tmp_path / "cp.db"))
    # 按 max(checkpoint_id) 倒序
    assert keys == [("1:new", "new"), ("1:old", "old")]


def test_list_user_thread_keys_empty(tmp_path):
    _checkpoint_db(tmp_path, [("2:other", "a")])
    assert list_user_thread_keys(1, db_path=str(tmp_path / "cp.db")) == []


# ---------------------------------------------------------------- 断流污染自愈（任务 #6）


def test_orphaned_tool_calls_detects_missing_tool_message():
    """孤儿检测：AI 带 tool_call 但无对应 ToolMessage → 返回该调用。"""
    msgs = [
        HumanMessage(content="hi"),
        _ai_msg("", tool_calls=[{"name": "web_search", "args": {"query": "x"}, "id": "c1", "type": "tool_call"}]),
        _ai_msg("结果"),  # 仅文本，无 ToolMessage 补 c1
    ]
    assert [c.get("id") for c in _orphaned_tool_calls(msgs)] == ["c1"]
    assert _orphaned_tool_calls(msgs)[0]["name"] == "web_search"


def test_orphaned_tool_calls_ignores_completed():
    """已有对应 ToolMessage 的 tool_call 不算孤儿。"""
    msgs = [
        _ai_msg("", tool_calls=[{"name": "web_search", "args": {"query": "x"}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="{}", tool_call_id="c1"),
    ]
    assert _orphaned_tool_calls(msgs) == []


def test_run_agent_chat_heals_orphaned_tool_call(tmp_path):
    """断流污染自愈：客户端在工具执行中断开 → 检查点留孤儿 tool_call（无 ToolMessage），
    LangGraph 次轮校验失败线程报废。run_agent_chat 应为孤儿补合成 ToolMessage，线程恢复
    合法，对话继续而非报错「Found AIMessages with tool_calls...」。

    在干净工具往返检查点基础上手工删除 ToolMessage 构造孤儿，验证次轮自愈（避免
    模拟断流的取消时序与 LangGraph 检查点锁竞争，测试确定性）。
    """
    thread = "t_poison"
    uid = 9

    async def _turn(model, msg):
        frames = []
        async for f in run_agent_chat(
            persona="student",
            user=UserContext(user_id=uid, role="student"),
            thread_id=thread,
            message=msg,
            llm_client=_client(model),
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    # 首轮：干净工具往返 → 合法检查点
    frames1 = asyncio.run(_turn(ScriptedToolModel(), "帮我查一下"))
    names1 = [n for n, _ in frames1]
    assert "error" not in names1 and names1[-1] == "done"

    # 构造孤儿：从检查点 messages 删除 ToolMessage（模拟工具执行中断流后缺结果）
    async def _orphanize():
        cp = _cp(tmp_path)
        async with cp as saver:
            cfg = make_agent_config(thread_key(uid, thread))
            tup = await saver.aget_tuple(cfg)
            assert tup is not None
            tup.checkpoint["channel_values"]["messages"] = [
                m for m in tup.checkpoint["channel_values"]["messages"]
                if getattr(m, "type", None) != "tool"
            ]
            await saver.aput(
                tup.config, tup.checkpoint, tup.metadata,
                tup.checkpoint.get("channel_versions") or {},
            )
            return _orphaned_tool_calls(await read_history(saver, cfg))

    orphans = asyncio.run(_orphanize())
    assert [o.get("id") for o in orphans] == ["c1"]

    # 次轮：自愈——补合成 ToolMessage，线程恢复，工具往返正常、无 error
    frames2 = asyncio.run(_turn(ScriptedToolModel(), "继续"))
    names2 = [n for n, _ in frames2]
    assert "error" not in names2
    assert names2[-1] == "done"
    texts2 = [p["content"] for n, p in frames2 if n == "text"]
    assert texts2 and texts2[-1] == "已联网查询"


# ---------------------------------------------------------------- 家长档案注入（方案 A：context 携带当前子女）

def _parent_child_ctx(db_session):
    school = School(name="测试学校", current_semester="2026-1")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = ClassModel(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    parent = Parent(name="张父", phone="13900000001")
    db_session.add(parent)
    db_session.flush()
    account = Account(
        username=f"parent_{parent.id}", password_hash="x",
        role=AccountRole.parent, role_id=parent.id,
    )
    db_session.add(account)
    db_session.flush()
    student = Student(class_id=cls.id, name="小明")
    db_session.add(student)
    db_session.flush()
    db_session.add(StudentParentBinding(
        parent_id=parent.id, student_id=student.id, bind_code="ABC",
        status="active",
    ))
    db_session.commit()
    return account, student


def test_build_memory_parent_injects_bound_child_profile(db_session):
    """家长绑定注入（§9.1）：context 带当前子女 student_id 且为 active 绑定 → 注入学生档案。"""
    from app.api.v1.agent import _build_memory

    account, student = _parent_child_ctx(db_session)
    mem = _build_memory(
        db_session, UserContext(user_id=account.id, role="parent"),
        {"student_id": student.id},
    )
    msg = mem.profile_system_message()
    assert msg is not None
    assert "学生档案" in msg["content"]
    assert "小明" in msg["content"]


def test_build_memory_parent_unbound_child_no_profile(db_session):
    """未绑定子女：不注入档案（端点层另行 403，此处保证不误注入）。"""
    from app.api.v1.agent import _build_memory

    account, _ = _parent_child_ctx(db_session)
    mem = _build_memory(
        db_session, UserContext(user_id=account.id, role="parent"),
        {"student_id": 999999},
    )
    assert mem.profile_system_message() is None


def test_build_memory_parent_no_context_single_child_auto_injects(db_session):
    """无 context / 无 student_id 但家长仅绑定 1 个 active 子女 → 自动注入该子女档案
    （避免 LLM 在无档案时拿 `${student_context}` 占位符去查学生）。"""
    from app.api.v1.agent import _build_memory

    account, student = _parent_child_ctx(db_session)
    mem = _build_memory(db_session, UserContext(user_id=account.id, role="parent"), None)
    msg = mem.profile_system_message()
    assert msg is not None
    assert "学生档案" in msg["content"]
    assert "小明" in msg["content"]


def test_build_memory_parent_no_context_multi_child_no_profile(db_session):
    """多个 active 子女且未显式指定 → 不自动注入（避免猜错孩子）。"""
    from app.api.v1.agent import _build_memory

    account, student = _parent_child_ctx(db_session)
    other = Student(class_id=student.class_id, name="小红")
    db_session.add(other)
    db_session.flush()
    db_session.add(StudentParentBinding(
        parent_id=account.role_id, student_id=other.id, bind_code="DEF",
        status="active",
    ))
    db_session.commit()
    mem = _build_memory(db_session, UserContext(user_id=account.id, role="parent"), None)
    assert mem.profile_system_message() is None


def test_build_memory_parent_no_context_no_children_no_profile(db_session):
    """家长无任何绑定子女 → 不注入档案。"""
    from app.api.v1.agent import _build_memory

    parent = Parent(name="独父", phone="13900000009")
    db_session.add(parent)
    db_session.flush()
    account = Account(
        username=f"parent_{parent.id}", password_hash="x",
        role=AccountRole.parent, role_id=parent.id,
    )
    db_session.add(account)
    db_session.commit()
    mem = _build_memory(db_session, UserContext(user_id=account.id, role="parent"), None)
    assert mem.profile_system_message() is None


def test_run_agent_chat_heals_missing_parent_profile(tmp_path):
    """旧线程自愈：历史无「学生档案」System 时本轮补注入（家长绑定修复前创建的存量会话）。
    新线程首轮注入后，次轮历史已含档案 → 不重复注入。"""
    from app.agents.memory import MemorySystem

    thread = "t_heal"
    uid = 7
    model = CapturingModel()

    async def _turn(mem, msg):
        frames = []
        async for f in run_agent_chat(
            persona="parent",
            user=UserContext(user_id=uid, role="parent"),
            thread_id=thread,
            message=msg,
            llm_client=_client(model),
            memory=mem,
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    # 首轮：无档案的 MemorySystem（模拟修复前旧线程）
    asyncio.run(_turn(MemorySystem(), "你好"))
    first_calls = len(model.calls)
    sys_first = " ".join(
        m.content for m in model.calls[first_calls - 1]
        if getattr(m, "type", None) == "system"
    )
    assert "学生档案" not in sys_first

    # 次轮：绑定孩子档案 → 历史缺档案应补注入
    mem2 = MemorySystem()
    mem2.bind_student_profile({"student_id": 1, "name": "小明", "class_id": 5, "class_name": "高一(1)班"})
    asyncio.run(_turn(mem2, "哪些知识点比较薄弱"))

    sys_second = " ".join(
        m.content for m in model.calls[-1]
        if getattr(m, "type", None) == "system"
    )
    assert "学生档案" in sys_second
    assert "小明" in sys_second
