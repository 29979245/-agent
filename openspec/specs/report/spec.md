## Purpose

提供学生「我的」页（doc 40 §2.5 个人报告与设置）的后端数据聚合端点：一次请求返回个人信息卡、学习统计卡、学习周报与学习计划，学生仅可读取自身的报告数据。

## Requirements

### Requirement: 学生个人报告聚合
系统 SHALL 提供 `GET /api/report/student/{student_id}` 端点，聚合返回学生的个人资料、学习统计、学习周报与学习计划；仅已认证角色可访问，学生角色仅可读取自身（非本人返回 403）。

#### Scenario: 本人读取报告成功
- **WHEN** 学生携带有效 token 请求 `GET /api/report/student/{student_id}` 且 `student_id` 等于自身业务实体 ID
- **THEN** 返回 200，响应含 `profile`（name/class_name/bind_code）、`stats`（completed_exercises/accuracy/streak_days）、`weekly`（week_label/exercises/accuracy/duration_hours/knowledge_points/teacher_comment）与 `learning_plan`

#### Scenario: 非本人读取被拒绝
- **WHEN** 学生携带有效 token 请求其他学生的 `student_id`
- **THEN** 返回 403 无权限，不泄露任何报告数据

#### Scenario: 教师读取学生报告
- **WHEN** 教师携带有效 token 请求某学生的报告
- **THEN** 返回 200，响应结构与学生本人读取一致（教师按权限矩阵拥有 report 读权限）

#### Scenario: 学生无任何作答记录
- **WHEN** 学生从未完成任何练习即请求报告
- **THEN** 返回 200，`stats` 各指标为 0、`weekly.knowledge_points` 为空数组、`learning_plan` 为 `null`，`weekly.teacher_comment` 为 `null`

### Requirement: 学习统计口径
系统 SHALL 在学习统计与学习周报中基于练习记录（`ExamRecord`，type=practice，排除 training/variant 模式）与学生作答（`StudentAnswer`）计算指标：`completed_exercises` 为已完成练习数、`accuracy` 为正确率（答对/总作答）、`streak_days` 为截至今日连续有作答的天数、`weekly.exercises` 与 `weekly.accuracy` 仅统计本周（本周一至今）。

#### Scenario: 统计基于练习记录
- **WHEN** 学生已完成若干练习并留下作答记录
- **THEN** 各统计指标按上述口径计算，training/variant 会话不计入

### Requirement: 知识点掌握度
系统 SHALL 按知识点聚合学生作答正确率，`weekly.knowledge_points` 为知识点名称到掌握度的数组，按题目知识标签（`Question.knowledge_points` 拆分）聚合。

#### Scenario: 知识点聚合
- **WHEN** 学生本周在不同知识点下有多道作答
- **THEN** `weekly.knowledge_points` 每项含 `name` 与 `mastery`（该知识点答对比例，0-1）

### Requirement: 学习计划返回
系统 SHALL 在报告响应中返回学生学习计划，读取 `Student.learning_plan`；无计划时返回 `null`。

#### Scenario: 已有学习计划
- **WHEN** 学生的学习计划字段非空
- **THEN** 响应 `learning_plan` 为计划对象原样返回

#### Scenario: 无学习计划
- **WHEN** 学生的学习计划字段为空
- **THEN** 响应 `learning_plan` 为 `null`

### Requirement: 周报教师评语占位
系统 SHALL 在学习周报中返回 `teacher_comment` 字段，当前固定为 `null`（LLM 评语生成属后续 `weekly_report` 工具范围）。

#### Scenario: 评语为空
- **WHEN** 学生读取报告
- **THEN** 响应 `weekly.teacher_comment` 为 `null`
