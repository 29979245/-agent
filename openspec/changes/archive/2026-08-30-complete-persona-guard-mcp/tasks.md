## 1. 浏览器工具组

- [x] 1.1 实现 `app/agents/tools/tools_browser.py`：`BrowserSession` 单例管理器（惰性启动 headless Chromium、60s 空闲回收）+ 5 个工具 `browse_navigate/read/click/input/screenshot`（async，mock Playwright 单测通过），用 `pytest tests/unit/test_agent_tools_browser.py` 验证
- [x] 1.2 在 `app/agents/tools/tool_meta.py` 注册 5 条 `ToolMeta(personas=PERSONAS, call_limit=3)`，用 `python -c "from app.agents.tools.tool_meta import TOOL_META; assert len(TOOL_META)==35"` 验证
- [x] 1.3 4 个 Persona YAML（teacher/student/tutor/parent）`available_skills` 追加 `browse_*` 5 工具，用 `python -c "from app.agents.personas.loader import effective_skills; [print(n, len(effective_skills(n))) for n in ('teacher','student','tutor','parent')]"` 验证各 Persona 含浏览器工具
- [x] 1.4 更新 `app/evals/agent_tools.py`：`registry_total_is_30` → 35、`STUDENT_TOOLS_EXACT` +5 浏览器、新增「全角色含 5 浏览器工具」检查，用 `run_evals --tier l1`（agent_tools 归入 l1 层）验证

## 2. GuardState 加固

- [x] 2.1 扩展 `app/agents/guard.py`：L1 新增 `check_context(ctx)`（ToolContext 缺 user/role 返回 `missing_prerequisites`），L2 新增 Token Bucket + `max_concurrent` 并发在途校验（执行开始/结束挂钩），L3 `_execution_keys` 改为带时间戳、`dedup_window_s` 过期可重试，用 `pytest tests/unit/test_agent_guard.py -q` 验证
- [x] 2.2 `app/agents/tools/registry.py` `execute_tool` 挂钩并发计数（try/finally 增减在途），审批工具与既有 35 工具路径行为不变，用 `pytest tests/unit/test_agent_tools_registry.py -q` 验证
- [x] 2.3 扩展 `tests/unit/test_agent_guard.py`：新增 Token Bucket 限速、并发超限、去重窗口过期重放 3 组用例，用 `pytest tests/unit/test_agent_guard.py -q` 验证

## 3. 规格同步与清理

- [x] 3.1 将 `specs/agent-core/spec.md` delta 合并进 `openspec/specs/agent-core/spec.md`：ADDED「浏览器工具组」、MODIFIED「Guard 四层护栏」「Persona 工具过滤」，用 `openspec validate --changes complete-persona-guard-mcp` 验证
- [x] 3.2 删除作废变更目录 `openspec/changes/retire-mcp-http-layer/`（代码改动已全部还原，无未提交依赖），用 `ls openspec/changes/` 确认仅剩完整变更

## 4. 回归与收尾

- [x] 4.1 全量 `pytest tests/ --tb=short` 通过（1210 passed，含 MCP 17 用例确认还原完整），无 GuardState 回归
- [x] 4.2 `run_evals --tier all --compare baseline.json` 通过（agent_tools 仍 100%，基线数值一致无需改），且 `graphify update .` 刷新知识图谱
