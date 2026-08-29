## Context

诊断与学生 7 工具已在 `app/agents/tools/tools_diagnosis.py` 落地（见 proposal.md - Why）。现状关键点：

- `assign_adaptive_practice`（`tools_diagnosis.py:332`）调用 `AdaptivePracticeService.generate_batch`（`app/services/exercise/adaptive.py:122`），后者经 `generate_for_student`（`:95`）创建 `ExamRecord` 并复制题目入库——违反 doc 28 §六"Agent 版本不写数据库，确认后由前端调用 API 持久化"。
- Guard 四层（`guard.py`）为前置/限次/去重/审批，无 data-access 层；`diagnose_barrier` 的家长门控 `_assert_parent_ok`（`tools_diagnosis.py:120`）仅个体路径调用，班级分支（`:212`）未接入，parent 携带 `class_id` 可拉全班学生画像。
- `APPROVAL_TOOLS = {"assign_adaptive_practice", "delete_bank"}`（`guard.py:41`），工具审批依赖 Guard L4 + SSE `awaiting_approval` 阶段 + 前端审批卡片。
- `practice.py` 现有端点：`GET /student/{uid}/tasks`、`POST /generate`（daily）、`POST /submit`、`GET /effect/{student_id}`——无班级批量自适应落库端点。

## Goals / Non-Goals

**Goals:**
- `assign_adaptive_practice` 变为 preview-only：返回每生预览（ZPD/难度/障碍/知识点/选中题目引用），零 DB 写入。
- 新增确认落库 REST 端点，教师显式确认后逐生持久化，作为 doc 28 §六的落库路径。
- 家长角色班级诊断返回 `ForbiddenError`，班级统计仅 teacher。
- 全部复用既有组件（AdaptivePracticeService 抽样、copy_historical_question、权限矩阵），零新增依赖。

**Non-Goals:**
- 不重写 `adaptive.py` 抽样/计算引擎逻辑（仅新增 preview/persist 接线）。
- 不做 `weak_knowledge_points` 透出、`generate_learning_plan` 三段式路由、distribution 均值语义调整（探索期判为非缺口/低优先）。
- 不新增审批状态表或审批 API（教师 UI 确认即授权，见 D2）。

## Decisions

### D1 — 引擎新增 preview/persist 两条路径
`AdaptivePracticeService` 新增：
- `preview_batch(student_ids, count, ...) → {results:[{student_id, zpd_difficulty, difficulty, barrier, knowledge_points, weak_kps, question_count, shortfall, question_refs:[{ref_id, ...}]}], batch_limit, remaining}`：复用 `plan_for` + `sample_questions` 得到每生预览，**不创建 ExamRecord、不复制题目**。沿用批次限制 5 人语义（spec adaptive-practice-engine）。
- `persist_batch(items, name="自适应练习") → {results:[{student_id, exam_id, practice_id, question_count}]}`：接收 `items:[{student_id, question_refs:[ref_id]}]`，校验学生存在 + refs 对应可复制题目，逐生创建 `ExamRecord`（`exam_type=practice`）并经 `copy_historical_question` 复制选中题目，返回逐生 `practice_id`。
- `generate_for_student`/`generate_batch` 保留不动（daily 调度仍用）。
- **备选**：confirm 端点重新调用 `generate_batch` 落库——抽样非确定性导致"教师确认的题目 ≠ 实际落库题目"，弃。
- **权衡**：`persist_batch` 严格按前端确认时携带的 `question_refs` 落库（所见即所得），须校验 refs 合法性防伪造。

### D2 — 审批从工具移至 API 确认（教师显式确认即授权）
- `tool_meta.py`：`assign_adaptive_practice` 移除 `approval=True`（预览无副作用）；描述改为"生成个性化 ZPD 练习预览，确认后由前端调用 API 落库"。
- `guard.py`：`APPROVAL_TOOLS` 收窄为 `frozenset({"delete_bank"})`。
- `POST /api/practice/adaptive/confirm`：`@require_permission("practice", "create")`（教师角色）；请求 `{class_id, items:[{student_id, question_refs}]}` → `persist_batch` 落库。教师在前端确认下发即授权，不再走 Guard L4 审批卡片。
- **备选**：保留工具审批、双段确认（审批卡片 → 预览 → 再调 API）——流程重复，弃。
- **权衡**：破坏性落库的授权点从"工具调用"变为"API 调用"，教师 UI 确认即批准；spec practice-api"需审批门控"解读为教师角色权限 + 显式确认动作。

### D3 — diagnose_barrier 家长班级门控
`diagnose_barrier` 顶部、班级分支前加：
```python
if _user_role(ctx) == "parent" and (class_id or class_name):
    raise ForbiddenError(detail="家长仅支持个体诊断")
```
- Guard `PREREQUISITES` 不变（班级参数仍满足"至少一标识非空"），工具层抛 `ForbiddenError` → `execute_tool` 捕获转 `{error:"forbidden", _guard_error:True}`。
- 家长携带 student+class 双参数：class 参数即越权意图，同样拒绝（个体诊断单独传 student 不受影响）。
- **备选**：Guard 增 data-access 层全局过滤——改动 Guard 核心，影响面大，本期工具层门控足够，弃。

### D4 — 前端最小接线（ai-tutor.html）
- `renderToolResult` 新增自适应预览分支：检测返回含 `results` 且每项含 `question_refs` → 渲染每生预览卡片（学生/难度/知识点/题数）+ **"确认下发"按钮**。
- 按钮点击：收集 `items:[{student_id, question_refs}]` + `class_id` → `POST /api/practice/adaptive/confirm` → 刷新/提示成功。
- **备选**：前端完全不做、仅后端就绪——"布置"功能端到端断链（预览无处落库），本期做最小接线保闭环。

## Risks / Trade-offs

- [confirm 信任前端 refs] → `persist_batch` 校验 student 属于请求班级、ref_id 存在且为可复制题目，非法即整批拒绝不落库。
- [preview 与 persist 分批重复触发] → preview 无副作用可安全重放；persist 仅由前端确认动作触发一次，Guard 去重不适用 API，靠教师显式确认防重复。
- [审批语义从"工具卡片"变"API 确认"后的产品认知变化] → 工具不再弹审批卡片，预览后由前端确认下发；E2E 冒烟断言确认端点 403（非教师）与落库成功两条路径。
- [adaptive.py 新增方法回归] → `preview_batch` 与既有 `generate_for_student` 共用 `plan_for`/`sample_questions`，既有 daily 测试全覆盖抽样逻辑，新增方法复用不改动。

## Migration Plan

无数据迁移。按依赖序合入：D3（改动最小，独立验证）→ D1 preview（工具预览，可独立单测）→ D2 审批移除 + confirm 端点（依赖 persist_batch）→ D4 前端接线 → 测试收尾。回滚：单 commit 可逆，`git revert` 回到工具写库现状（工具已存在，无结构性变更）。

## Open Questions

无。D1-D4 均按现有代码结构确认可行。`persist_batch` 所需 `question_refs` 语义（历史题 ref 还是库题 id）在实现时以 `sample_questions` 返回的 ref 类型为准，不改变规格与任务分解。
