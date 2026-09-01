## Context

学习计划「生成」链路当前是断的（见 proposal.md — Why）：`Student.learning_plan` 唯一写入方是教师专用 Agent 工具 `send_learning_plan`（`tools_diagnosis.py:365`），`generate_learning_plan` 只是跳转占位；doc 22 规格的 REST 端点不存在；教师端 `students.html:241` 按钮 `disabled`；学生端 `profile.html` 空态文案「完成练习后可生成」与事实不符。

关键约束：
- 诊断 LLM 仍是桩（`llm_diagnosis._chat` 立即报错 → 走规则单侧），计划生成**不能依赖 LLM**。
- 计划数据源已具备：`report_service.build_knowledge_points`（知识点掌握度）、`student.barrier_profile`（三维障碍）、`practice_answers`（作答统计）。
- `Student.learning_plan` 是 `MutableDict` JSON 列，无需迁移；`report` 端点已原样透传 `student.learning_plan`（`report_service.py:168`），落库即可见。
- 家长通知模式已有先例：`send_learning_plan`（`StudentParentBinding` active 查询 + `ParentNotification(NotificationType.learning_plan)`）。

## Goals / Non-Goals

**Goals:**
- 补全 doc 22 §2.6 规格的三个 REST 端点，全部走 `diagnosis` 权限矩阵。
- 规则引擎生成器：薄弱知识点（掌握度 < 阈值）排序 → 每日任务，确定性、可单测。
- 教师端两步交互（生成预览 → 确认推送）与深链 `?focus=<sid>&action=plan`。
- 学生端空态文案修正 + 新版 `plan_text + plan_data` 渲染。

**Non-Goals:**
- 不引入 LLM 生成（LLM 未接入）。
- 不做学习计划的历史版本管理（`send_learning_plan` 当前覆盖写入，保持一致）。
- 不改 `report` 端点契约（`learning_plan` 已透传，规格无需变更）。
- 不实现学生侧主动生成（回归设计，计划由教师产生）。

## Decisions

### D1：计划数据格式 — `plan_text + plan_data`（doc 30:788 新版）

```
{
  "title": "xxx 的学习计划（7 天）",
  "plan_text": "## 学习计划\n基于近期作答…优先复习：…",
  "plan_data": {
    "days": [
      {"label": "第1天", "tasks": ["复习知识点：氧化还原反应（基础练习 3 题）", "错题回顾 2 题"]},
      ...
    ]
  }
}
```
- 为什么：doc 30:788 明确「新版 plan_text+plan_data 格式」；plan_text 供富文本展示，plan_data.days 供逐日任务渲染。
- 备选：纯字符串 / 字符串数组——过于简单，承载不了薄弱点与逐日结构；对象键平铺（`{k: v}`）——profile 现有 fallback 会渲染出 `plan_data：[object Object]`，故需显式渲染分支。
- 兼容：`profile.renderPlan` 保留字符串/数组分支（既有格式不受影响）。

### D2：生成器为纯规则引擎（不走 LLM）

`app/services/diagnosis/learning_plan.py` 新增 `generate_plan(db, student_id) -> dict`：
1. 复用 `report_service.practice_answers` + `build_knowledge_points` 计算各知识点掌握度。
2. 薄弱知识点 = mastery < 0.6，按 mastery 升序取前 N（N=3）个；**无薄弱点（全部掌握达标）→ 退化为按 mastery 升序取前 N 个生成巩固计划**，保证有作答数据即有计划。
3. 障碍画像：`barrier_profile` 三维取最高（`dominant_axis`，聚合层共享）→ 生成一条针对性任务（如「重点练习审题类题目，注意圈划关键词」）。
4. 无作答数据 → 返回 `{empty: True, message: "暂无足够学习数据"}`（端点映射为 409/空计划提示）。
5. **兜底（仅练习-数据无知识点）**：作答题目均未标知识点（`knowledge_points` 为空，`focus` 仍为空）→ 返回单日「巩固练习计划」（第 1 天：错题回顾 + 综合练习）。
6. 输出 D1 格式：每日任务 = 薄弱点按天分摊（第 1 天最弱知识点 + 基础题，逐日推进到综合练习 + 错题回顾）。

- 为什么不用 LLM：诊断 LLM 是桩，规则版离线可测、零波动。
- 备选：让 LLM 在教师会话里拼 `plan_data` 再 `send_learning_plan`——不可控、难测试，且当前 LLM 不可用。

### D3：三个端点挂 `diagnosis_router`，权限走矩阵

| 端点 | 权限 | 行为 |
|---|---|---|
| `POST /api/diagnosis/learning-plan/generate` | `diagnosis:create`（教师+） | body `{student_id}` → 返回预览，**不落库不通知** |
| `POST /api/diagnosis/learning-plan/apply/{student_id}` | `diagnosis:create`（教师+） | body `{plan}` → 写 `student.learning_plan` + 家长通知 |
| `GET /api/diagnosis/learning-plan/{student_id}` | `diagnosis:read` | 学生仅读自身（他人 403），教师任意 |

- 为什么 `diagnosis:create`：矩阵中 teacher+ 有 create，student 只有 read，天然隔离（`permissions.py` ROLE_PERMISSIONS）。
- 所有权：get 端点由 `request.state.user` 判定——student 且 `user_id != 目标学生关联账号` → 403。（沿用 `history/{student_id}`「student 仅读自身」的既有模式。）

### D4：apply 复用家长通知逻辑（小重构）

`learning_plan.py` 提供 `apply_plan(db, student_id, plan)`：写 `student.learning_plan` + 按 `StudentParentBinding.status=="active"` 查绑定并创建 `ParentNotification(NotificationType.learning_plan)`。
- `tools_diagnosis.send_learning_plan` 改为调用 `apply_plan`（消除与端点重复的查询/通知代码）。
- 为什么：端点与 Agent 工具行为必须一致（同一通知渠道），单一实现避免漂移。

### D5：教师端交互与深链

`students.html`：
- 启用「生成学习计划」按钮（去 `disabled`），点击 → `POST generate` → 抽屉内新增 `#planPreview` 区渲染预览（plan_text Markdown + plan_data.days 逐日列表）+「推送给学生」按钮。
- 确认推送 → `POST apply` → toast「已推送」；失败（无作答数据 409）→ 显示「暂无足够学习数据，无法生成计划」。
- 深链：加载后解析 `?focus=<student_id>&action=plan` → 找到学生 → `openDrawer` + 自动触发 generate（对应 `generate_learning_plan` 工具返回的 `_route.params`）。
- `api.js` 新增 `generateLearningPlan(studentId)` / `applyLearningPlan(studentId, plan)`。

### D6：学生端文案与渲染

`profile.html`：
- 空态：`暂无学习计划，完成练习后可生成` → `暂无学习计划，等待老师为你生成`。
- `renderPlan` 新增 `plan_text + plan_data` 分支：`plan_text` 经 `renderMarkdown` 渲染，`plan_data.days` 逐日列表逐条展示；保留字符串/数组兼容分支。

## Risks / Trade-offs

- [薄弱点阈值 0.6 与取前 3 为硬编码] → 计划在数据不足时可能偏短；阈值/数量集中于生成器常量，后续可提为 `diagnosis/config` 可配（本轮不做，留作 Open Questions 级演进）。
- [deep-link 依赖 `students.html` 学生数据预加载（`loadAll`）] → 深链处理放在 `loadAll().then(...)` 之后，学生不在列表则静默（与既有 `?student=<name>` 一致）。
- [`send_learning_plan` 小重构有回归面] → 行为等价重构，由 `test_agent_tools_diagnosis.py` 既有用例守护。
- [apply 覆盖式写 `learning_plan`，无版本历史] → 与现状 `send_learning_plan` 一致，本轮不引入历史表（Non-Goal）。

## Migration Plan

- 无数据模型变更、无 Alembic 迁移（`Student.learning_plan` 已存在）。
- 部署：重启后端进程加载新端点；前端 HTML/CSS/JS 由 StaticFiles 逐请求读盘即时生效。
- 回滚：删除三个端点路由即可（无状态变更）；已推送计划保留在 `student.learning_plan` 无害。

## Open Questions

（无——所有会影响规格/方案/任务切分的问题已在上述决策中解决。）
