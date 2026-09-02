## ADDED Requirements

### Requirement: 学习计划生成与推送

系统 SHALL 提供学习计划 REST 端点，供教师为指定学生生成并推送学习计划、供学生与教师读取：
- `POST /api/diagnosis/learning-plan/generate`：教师 SHALL 提交 student_id，系统 SHALL 基于学生知识点掌握度（薄弱知识点）与障碍画像，以规则引擎确定性生成学习计划预览（标题 + plan_text + plan_data 每日任务列表）；生成 SHALL 不落库、不触发通知；学生无练习作答数据时 SHALL 返回空计划与提示。
- `POST /api/diagnosis/learning-plan/apply/{student_id}`：教师 SHALL 提交计划内容，系统 SHALL 写入 `Student.learning_plan` 并 SHALL 向该学生所有有效绑定的家长创建学习计划通知；推送后计划 SHALL 在学生报告端点可见。
- `GET /api/diagnosis/learning-plan/{student_id}`：读取计划；学生角色 SHALL 仅可读取自身（他人返回 403），教师角色 SHALL 可读取任意学生。
- 权限：generate/apply SHALL 要求 `diagnosis:create`（教师+ 角色），get SHALL 要求 `diagnosis:read`；未授权 SHALL 返回 403。

#### Scenario: 教师生成计划预览

- **WHEN** 教师提交某学生的 student_id 调用 generate
- **THEN** 返回含 title/plan_text/plan_data.days 的计划预览，`Student.learning_plan` 保持原值不变

#### Scenario: 薄弱知识点驱动计划内容

- **WHEN** 学生存在掌握度低于阈值的知识点
- **THEN** 计划每日任务覆盖这些薄弱知识点（按掌握度升序优先安排）

#### Scenario: 无薄弱知识点时生成巩固计划

- **WHEN** 学生有练习作答记录但无掌握度低于阈值的知识点（或作答题目均未标注知识点）
- **THEN** generate 仍返回计划：按掌握度升序取前 N 生成巩固计划；若知识点为空则返回单日「巩固练习计划」，不返回空计划

#### Scenario: 无练习作答数据

- **WHEN** 学生没有任何练习作答记录
- **THEN** generate 返回空计划与「暂无足够学习数据」提示

#### Scenario: 教师推送计划

- **WHEN** 教师提交计划内容调用 apply
- **THEN** `student.learning_plan` 更新为新内容，且该学生有效绑定的家长各收到一条学习计划通知

#### Scenario: 学生仅读自身

- **WHEN** student 角色调用 get 访问他人 student_id
- **THEN** 返回 403

#### Scenario: 越权生成/推送

- **WHEN** student 角色调用 generate 或 apply
- **THEN** 返回 403
