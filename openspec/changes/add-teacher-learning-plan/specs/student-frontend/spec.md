## MODIFIED Requirements

### Requirement: 学习周报与学习计划展示

系统 SHALL 使「我的」页「学习报告」入口以底部滑出弹窗展示学习周报：周标签、本周练习/正确率/学习时长概览、知识点掌握度条形图（按 `weekly.knowledge_points` 的 `name`/`mastery`）与教师评语；「学习计划」入口展示 `learning_plan` 计划内容，无计划时显示空态。学习计划 SHALL 支持新版 `plan_text + plan_data` 格式（标题 + 每日任务列表）与既有字符串/数组格式渲染。

#### Scenario: 周报弹窗渲染

- **WHEN** 学生点击「学习报告」且后端返回含知识点掌握度的周报
- **THEN** 弹窗展示周标签与概览数字，知识点按 `mastery` 比例渲染条形图，教师评语字段展示（为 null 时显示占位）

#### Scenario: 无学习计划空态

- **WHEN** `learning_plan` 为 `null`
- **THEN** 「学习计划」弹窗显示空态文案「暂无学习计划，等待老师为你生成」，不得暗示完成后自动生成

#### Scenario: 新版计划格式渲染

- **WHEN** `learning_plan` 为含 `plan_text` 与 `plan_data` 的对象（plan_data 含每日任务列表）
- **THEN** 弹窗渲染计划标题与每日任务列表（plan_text 经 Markdown 渲染、逐日任务逐条展示），不出现 JSON 原文

#### Scenario: 旧版计划格式兼容

- **WHEN** `learning_plan` 为字符串或字符串数组
- **THEN** 弹窗仍按既有格式渲染（字符串直接展示 / 数组逐条展示）
