## Context

出题组 7 工具已在 `app/agents/tools/tools_exam.py` 落地并注册进 `tool_meta.py`，但实现与设计文档 25/30 的契约存在 8 处缺口（见 proposal.md - Why）。现状关键点：

- `run_agent_chat`（`agent.py:157`）构造 `ToolContext` 时未传 `search`，故 `ctx.search` 恒为 None，`web_search` 与三级搜索 Tier-3 实际不可用；`app/agents/tools/search_client.py` 已有 `SearchClient` 与 `get_search_client()` 单例（读 `SEARCH_API_BASE/SEARCH_API_KEY`）。
- `_audit_question`（`tools_exam.py:224`）已调用四维引擎 `audit_equation`（`app/services/audit/equation.py:569`，返回 `EquationAuditReport`：balance/condition/product/structure 四维），但只消费 `report.balanced` 一维，其余三维判定被丢弃。
- `_rag_context`（`tools_exam.py:237`）只回传 `{content, answer}`，丢失向量命中的 RAG 元数据。
- `_emit_directives`（`registry.py:59`）对 `_route` dict 只发 `navigate{page, params}`，不支持三段式 `{navigate, populate, actions}` 拆分。
- SSE 12 事件协议（`sse_adapter.py`）已含 `navigate/populate/action/exam_images`，通道现成。

## Goals / Non-Goals

**Goals:**
- 让出题组工具完整满足设计文档契约：联网可用、四维审核全量生效、RAG 元数据回传、工作台/保存跳转协议对齐前端、搜索结构化过滤 + exam_images 事件。
- 全部复用既有组件（SearchClient、audit_equation、SSE 事件通道），零新增第三方依赖。

**Non-Goals:**
- 不实现前端面板本身（exam-workbench 已存在，仅对齐其消费契约）。
- 不重写 `audit_equation` 四维引擎逻辑（已确定性与完备，仅接线）。
- 不新增 MCP 工具或改动 MCP 注册表。
- 不改 Agent 核心循环、Guard 四层门控、审计/记忆链路。

## Decisions

### D1 — `ToolContext.search` 接线（联网可用）
`run_agent_chat` 构造 `ToolContext` 时补传 `search=get_search_client()`。`web_search`/Tier-3 已有降级分支（`ctx.search is None or not available` → 返回"未配置"），未配置环境自动降级，无需额外兜底。
- **备选**：按需懒加载 DI 工厂——过度设计，单例已存在，直接用。
- **权衡**：单例在测试中需 `monkeypatch` 注入 fake，单测注意 patch `get_search_client` 或直接构造 `ToolContext(search=fake)`。

### D2 — 四维审核全量启用（软标记）
`_audit_question` 消费 `EquationAuditReport` 全部四维：`balance/condition/product/structure`，audit 摘要返回四维各自的 `status/message/evidence`。判定遵循 doc 26 §6.3 软标记语义——**仅系数配平失败（blocked）才 `passed=False`**；条件/产物/结构问题为 warning 不阻断；`save_to_bank` 对 `passed=False`（配平失败）的题跳过并计入 `skipped_count`，warning 题正常入库且 `audit_status=warning`（前端三色徽章）。
- **备选**：用 LLM 二次审核——违背 CLAUDE.md"化学必须确定性算法"硬约束，弃。
- **权衡**：软标记避免"四维全开误伤边缘合法式（如条件可省略的反应）"——不合格仅配平硬拦截，其余维度带 evidence 交给 LLM 向用户解释；阈值/豁免规则沿用 `audit_equation` 现状，不在本变更内调整引擎。

### D3 — RAG 元数据 + 陷阱提示 + A/B 模式
- `_rag_context` 返回元素扩为 `{content, answer, is_from_rag, source_question_id, similarity, match_method}`：向量命中填 `is_from_rag=True` + 来源元数据，关键词回落填 `match_method="keyword"`。
- **A/B 模式**：`generate_questions` 按 `len(samples) >= 3` 分支——模式 A 提示词要求"参考样例题风格/设问角度出题"，模式 B 纯按知识点生成；返回结果 `mode: "A"|"B"` 标注。
- **陷阱提示**：`_GENERATE_SYSTEM_PROMPT` 增加字段指令（生成 `trap_hint` 说明设题陷阱点），后处理将 LLM 缺失的 `trap_hint` 置空串而非报错。
- 后处理将 RAG 元数据按索引附着到每道生成题。
- **备选**：独立 RAG 元数据工具——无必要，附着即满足 doc 25 §4.4。

### D4 — 工作台参数 `types` 数组
`show_exam_workbench` 参数与返回改为 `types:[{val, active, qty}]`（val=题型值、active=默认激活、qty=生成数量），`question_type` 单字符串字段移除；`TOOL_META` schema 同步更新。兼容性：历史 `question_type` 入参丢弃（由 LLM 提示词引导新结构，`tools_exam.py` 内做一次 `str→[{val,active:true,qty}]` 的容错映射，避免旧缓存参数炸掉）。

### D5 — `save_to_bank` 三段式跳转协议
`_route` 返回改为 `{navigate, page:"exam-v2", populate:{target:"exam-set", data:{set_id, set_name}}, actions:[{action:"openTab", payload:"bank"}]}`；同步扩展 `registry._emit_directives`：route dict 含 `navigate` 键时发 `navigate{page, params}`、含 `populate` 发 `populate{...}`、含 `actions` 逐条发 `action{...}`；兼容旧 `{page, params}` 两字段形式（保持 navigate 发射）。
- **权衡**：`_emit_directives` 是工具通用 emit 点，改动影响所有 `_route` 工具（当前仅 save_to_bank），兼容分支保证无回归。

### D6 — 搜索结构化过滤 + 0.6 阈值 + `exam_images`
- `search_exam_bank` 新增可选 `source/region/knowledge_point` 过滤，三级搜索各层都套用。
- 向量召回阈值常量 `VECTOR_SIM_THRESHOLD = 0.6`，低于阈值的命中不入列、不计入"可用真题数"（决定 Tier-3 是否触发）；阈值仅约束 ChromaDB 向量命中（带 similarity），ChromaDB 不可用降级为关键词匹配的命中无相似度、不适用阈值（doc 25 §7.4）。
- 命中的题目含图片字段时经 `ctx.emit("exam_images", {...urls})` 发射事件（sse_adapter 已支持，经 emit_queue 走 Guard 剥离通道）。
- **备选**：把 exam_images 塞进 tool_result——违背前端富渲染契约（图片需独立事件），弃。
- **数据依赖（本轮降级，不做空字段）**：当前 Question 模型、向量 metadata、真题库均无图片字段，`exam_images` 为预留事件通道——无图命中时自然不触发。题目配图为独立需求，数据源（导入/上传）就绪后在 Question/向量 metadata 增加 `image_url` 字段并透传即可启用，无需改工具代码。

### D7 — TOOL_META schema 与测试
`tool_meta.py` 同步 `SearchExamBankArgs`（+source/region/knowledge_point）、`ShowExamWorkbenchArgs`（types 数组）、`SaveToBankArgs` schema；单测覆盖 D1-D6 全部契约（含 `ctx.search=None` 降级分支、仅配平失败阻断、RAG 元数据存在、三段式 _route 拆分、exam_images 发射），集成测试验证 SSE 事件序列。

## Risks / Trade-offs

- [软标记四维判定] → 仅配平失败硬拦截；条件/产物/结构带 warning + evidence，LLM 可向用户说明；不阻断整个批量，warning 题正常入库并标三色徽章。
- [search_client 未配置环境联网静默失效] → 保留"未配置"降级文案，前端可见；E2E 冒烟在无 key 环境断言降级分支而非真实联网。
- [`_emit_directives` 通用改动回归] → 兼容旧 `{page, params}` 形式，出题组集成测试断言事件序列。
- [types 数组破坏旧前端] → 前端与后端同仓同版发布；旧 `question_type` 入参容错映射保底。

## Migration Plan

无数据迁移。按依赖序合入：先 D1（接线，改动最小可独立验证）→ D6 → D2/D3（生成链路）→ D4/D5（前端契约）→ D7（测试收尾）。回滚：单 commit 可逆，`git revert` 即可回到现状（工具已存在，无结构性变更）。

## Open Questions

无。D1-D7 均已按现有代码结构确认可行，不依赖外部决策。
