## Why

课程「Persona 系统 + GuardState 护栏 + MCP 服务器」实现清单要求补齐三类能力。经逐项核对，现状为：

- **浏览器工具组（5 工具）缺失**：doc 30 §3.8 定义了 `browse_navigate/read/click/input/screenshot` 5 个浏览器工具（所有角色可用，单实例 + 60s 空闲回收），但 `app/agents/tools/` 中零实现，Persona 白名单也不含 `browse_*`——doc 30 §4「所有 Persona 自动附加浏览器工具」的承诺未落地。
- **GuardState 四层为 doc 30 §5 的简化实现**：L1 仅参数前置、L2 仅每工具计数、L3 请求级生命周期去重；课程清单要求的 L1 会话/认证上下文检查、L2 Token Bucket + 并发限制、L3 去重超时窗口均未实现（注：这三项**超出 doc 30 §5 原文**，为课程验收点的强化要求）。
- **MCP 工具服务器**：本会话曾按 1B 决策收编，随后按用户决定「§12 原样恢复」——代码/端点/测试/规格/ADR 已全部还原，`test_mcp_api.py` 17 用例通过。本变更对 MCP 仅做恢复确认，不再改动。

## What Changes

- **新增浏览器工具组**：实现 `browse_navigate` / `browse_read` / `browse_click` / `browse_input` / `browse_screenshot`（`app/agents/tools/tools_browser.py`），基于 Playwright（`requirements.txt` 已有 `playwright>=1.40`）单浏览器实例 + 60s 空闲超时回收（doc 30 §3.8）；注册进 TOOL_META（`personas=PERSONAS` 全角色），4 个 Persona YAML 白名单追加 `browse_*`；工具总数 30 → 35。
- **GuardState 加固**（超出 doc 30，课程验收点）：
  - L1 前置条件追加**会话/认证上下文检查**——ToolContext 缺 user 身份/角色时返回结构化错误；
  - L2 由计数限次升级为 **Token Bucket + 并发调用限制**（并发上限定义于 TOOL_META 或 GuardState 构造参数）；
  - L3 去重键引入**超时窗口**——窗口过期后相同调用可再次执行（原为请求级生命周期永久去重）。
- **MCP 恢复确认**：无代码改动，任务仅含「还原后 test_mcp_api.py 通过 + 端点冒烟」。
- **遗留清理**：`retire-mcp-http-layer` 变更目录已作废（决策被本会话反转），从 changes 目录移除。

## Capabilities

### New Capabilities

无独立新 capability——浏览器工具组作为 agent-core 内新增需求（沿用该 spec 现有「工具切片」组织方式）。

### Modified Capabilities

- `agent-core`: 
  - **ADDED**「Requirement: 浏览器工具组」（5 工具 + 单实例生命周期 + 全角色可用）；
  - **MODIFIED**「Requirement: Guard 四层护栏」（L1 加会话/认证上下文检查、L2 加 Token Bucket + 并发限制、L3 加去重超时窗口）。

## Impact

- **代码**：新增 `app/agents/tools/tools_browser.py`（Playwright 封装 + 5 工具）；`tool_meta.py` 注册 5 条元数据（`personas=PERSONAS`）；4 个 Persona YAML 白名单追加 `browse_*`；`guard.py` 扩展 L1/L2/L3（向后兼容既有 30 工具路径）。
- **测试**：新增浏览器工具单测（导航/读取/点击/输入/截图 + 60s 回收，mock Playwright）；扩展 `test_agent_guard.py`（Token Bucket 限速、并发超限、去重窗口过期重放）；更新 `app/evals/agent_tools.py`（`registry_total_is_30` → 35、student 精确工具集含浏览器、全角色浏览器可用检查）。
- **规格**：`openspec/specs/agent-core/spec.md` 新增浏览器工具组需求、修改 Guard 四层护栏需求。
- **文档**：`docs/adr/` 无新增决策（GuardState 强化为既有 D11 语义扩展）；doc 30 §12 停泊设计维持原样。
- **无影响面**：MCP（已恢复，不动）、调度器、前端、既有 30 工具路径（GuardState 向后兼容）。
