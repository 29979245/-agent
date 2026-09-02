## ADDED Requirements

### Requirement: 教师生成学习计划

系统 SHALL 在学生详情抽屉提供「生成学习计划」操作：教师点击 SHALL 调用生成端点获取计划预览并在抽屉内展示；教师确认后 SHALL 调用推送端点将计划发给学生。页面 SHALL 支持深链 `?focus=<student_id>&action=plan`：加载后自动打开对应学生抽屉并触发生成（对应 `generate_learning_plan` Agent 工具的路由指令）。

#### Scenario: 生成并预览计划

- **WHEN** 教师点击学生详情抽屉的「生成学习计划」
- **THEN** 抽屉内展示规则生成的学习计划预览（标题 + 每日任务），并出现「推送给学生」确认操作

#### Scenario: 推送计划

- **WHEN** 教师确认推送预览计划
- **THEN** 调用 apply 端点，成功后提示已推送，计划对学生可见

#### Scenario: 深链自动触发生成

- **WHEN** 教师从 AI 助手经 `generate_learning_plan` 跳转到 `?focus=<student_id>&action=plan`
- **THEN** 页面加载后自动打开该学生抽屉并触发「生成学习计划」

#### Scenario: 学生无作答数据

- **WHEN** 目标学生没有任何作答记录
- **THEN** 抽屉内显示「暂无足够学习数据，无法生成计划」提示
