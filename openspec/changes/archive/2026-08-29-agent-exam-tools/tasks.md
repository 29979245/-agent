## 1. 联网接线（D1）

- [x] 1.1 在 `app/agents/agent.py` 的 `run_agent_chat` 构造 `ToolContext` 时补传 `search=get_search_client()`，并确认 `web_search`/Tier-3 在 `ctx.search=None or not available` 时返回"未配置"降级、不抛错（新增单测：无搜索配置时 `web_search` 返回降级提示，对话不中断）
- [x] 1.2 新增 `search_exam_bank` Tier-3 联网补齐分支单测：`ctx.search.available=True` 时本地不足 3 条触发联网、结果标记"AI辅助搜索（本地题库仅 N 道，以下为AI补充）"

## 2. 搜索契约升级（D6）

- [x] 2.1 `SearchExamBankArgs` 增加可选 `source / region / knowledge_point` 过滤字段，三级搜索各层套用并单测断言过滤生效
- [x] 2.2 向量召回增加阈值 `VECTOR_SIM_THRESHOLD = 0.6`，低于阈值命中不入列、不计入可用真题数，单测断言临界值边界
- [x] 2.3 命中的题目含图片字段时经 `ctx.emit("exam_images", {...})` 发射事件，集成测试断言 SSE 出现 `exam_images` 事件且 `tool_result` 不含图片字段

## 3. 生成链路升级（D2 + D3）

- [x] 3.1 `_audit_question` 消费 `EquationAuditReport` 全部四维（balance/condition/product/structure），仅配平失败硬阻断 `passed=False`，条件/产物/结构软标记 warning 不阻断；每维返回 status/evidence（doc 26 §6.3）
- [x] 3.2 `save_to_bank` 对 `audit.passed=False` 的题目跳过入库并计入 `skipped_count`，单测断言不合格题不入库
- [x] 3.3 `_rag_context` 返回元素扩为含 `is_from_rag / source_question_id / similarity / match_method`，生成题按索引附着 RAG 元数据；单测断言元数据字段存在且值正确
- [x] 3.4 `generate_questions` 按 `len(samples) >= 3` 分支 A/B 模式（A 参考真题风格 / B 纯生成），返回带 `mode` 标注；单测分别断言两种模式
- [x] 3.5 `_GENERATE_SYSTEM_PROMPT` 增加 `trap_hint` 字段指令，后处理对缺失的 `trap_hint` 置空串；单测断言返回题含 `trap_hint` 且格式稳定

## 4. 前端契约对齐（D4 + D5）

- [x] 4.1 `show_exam_workbench` 参数与返回改为 `types:[{val, active, qty}]` 数组，兼容旧 `question_type` 字符串入参（容错映射 `str→[{val,active:true,qty}]`）；单测断言 `types` 结构与 qty 传递正确
- [x] 4.2 `save_to_bank` 的 `_route` 改为 `{navigate, page:"exam-v2", populate:{target:"exam-set", data:{set_id,set_name}}, actions:[{action:"openTab", payload:"bank"}]}`；单测断言 `_route` 结构
- [x] 4.3 扩展 `app/agents/tools/registry.py` `_emit_directives`：route dict 含 `navigate/populate/actions` 键时分别发 `navigate/populate/action` SSE 事件，兼容旧 `{page, params}` 形式；集成测试断言事件序列 `navigate → populate → action`

## 5. Schema 与测试收尾（D7）

- [x] 5.1 `app/agents/tools/tool_meta.py` 同步更新 `SearchExamBankArgs`（+source/region/knowledge_point）、`ShowExamWorkbenchArgs`（types 数组）、`SaveToBankArgs` schema，并断言 TOOL_META 校验通过
- [x] 5.2 更新 `tests/unit/test_agent_tools_exam.py` 覆盖 D1-D6 全部契约（含 `ctx.search=None` 降级、四维任一失败阻断、RAG 元数据、三段式 _route、exam_images）
- [x] 5.3 运行 `pytest tests/unit/test_agent_tools_exam.py tests/integration/ -q` 全部通过，并跑 `pytest tests/ --tb=short -q` 确认无回归

## 6. 验证与收尾

- [x] 6.1 在无 LLM/搜索 key 环境跑 `python scripts/smoke_agent_e2e.py`，断言降级路径（web_search 未配置提示、chat error+done 干净收尾）与既有 12 项检查全部 PASS
- [ ] 6.2 配置搜索 API key 后人工复核 `web_search`/Tier-3 真实联网（HTTP 级 curl 断言返回 `summary/source_count/urls`）
- [x] 6.3 运行 `graphify update .` 保持知识图谱同步，确认无残留 TODO/FIXME
