## Why

出题组 7 个 Agent 工具（search_exam_bank / web_search / show_exam_workbench / generate_questions / save_to_bank / list_banks / delete_bank）已在 `tools_exam.py` 落地并注册进 TOOL_META，但当前实现与设计文档 25/30 的工具契约存在 8 处缺口：联网搜索未接线（`ToolContext.search` 恒为 None，web_search 与三级搜索 Tier-3 实际不可用）、generate_questions 只做方程配平单维审核、RAG 元数据未回传、缺陷阱提示、提示词不分支 A/B 模式、工作台参数结构、保存跳转协议、搜索结构化过滤均与前端富渲染契约不符。不改则出题链路"搜索→生成→审核→保存→渲染"无法闭环，前端面板无法正确消费工具返回。

## What Changes

- **打通联网搜索**：`ToolContext.search` 注入 `SearchClient`（agent.py 构建上下文处补参）；`web_search` 与 `search_exam_bank` Tier-3 在未配置搜索 API 时优雅降级（保留"未配置"提示，不抛错）。
- **generate_questions 升级为完整四维审核**：除方程配平外补全反应条件、产物、分子结构三维修正，每维返回 evidence 与判定；仅四维全过才标记可保存。
- **RAG 元数据回传**：生成题目携带 `is_from_rag / source_question_id / similarity / match_method`，与 doc 25 §4.4 一致。
- **陷阱提示**：AI 生成题目附带 `trap_hint`（陷阱点说明），随题目预览返回。
- **提示词 A/B 模式分支**：检索真题 ≥3 条走模式 A（风格参考真题），否则走模式 B（纯生成），影响生成提示词与结果标注。
- **工作台参数结构**：`show_exam_workbench` 的 `question_type` 改为 `types:[{val, active, qty}]` 数组（doc 25 §11.3）。
- **保存跳转协议**：`save_to_bank` 的 `_route` 改为三段式 `{navigate, page:"exam-v2", populate, actions:[{action:"openTab", payload:"bank"}]}`（doc 25 §5.4）。
- **搜索结构化过滤**：`search_exam_bank` 增加 `source / region / knowledge_point` 可选过滤，向量检索阈值收紧至 0.6，命中图片题型时发射 `exam_images` SSE 事件（doc 25 §7.4）。
- **补测试**：新增/修订出题组 7 工具单测与集成测试，覆盖上述契约（含 web_search 未接线时的降级分支）。

## Capabilities

### New Capabilities
<!-- 本变更不引入新能力，全部落在既有 agent-core 出题组工具契约内 -->

### Modified Capabilities
- `agent-core`: 工具切片——出题组需求细化——web_search/三级搜索联网接线与降级、generate_questions 四维审核+陷阱提示+RAG 元数据+A/B 模式、工作台参数 `types` 数组、保存跳转三段式协议、搜索结构化过滤与 0.6 阈值、exam_images 事件。

## Impact

- **代码**：`app/agents/agent.py`（ToolContext.search 注入）、`app/agents/tools/tools_exam.py`（6 处工具契约升级）、`app/agents/tools/search_client.py`（接线确认）、`app/agents/tools/tool_meta.py`（schema 更新）、`app/agents/sse_adapter.py`（exam_images 事件发射）、`app/agents/guard.py`（`_component/_route` 剥离不变，新增字段放行）。
- **前端**：`frontend/` exam-workbench 面板按 `types` 数组与三段式跳转协议消费；exam_images 事件渲染。
- **测试**：`tests/unit/test_agent_tools_exam.py`、`tests/integration/`（新增出题契约用例）。
- **依赖**：无需新增第三方库（SearchClient 已存在，仅接线）。
