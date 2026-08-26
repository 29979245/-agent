## Purpose

提供学生练习的 REST 接口（任务列表 / 提交批改 / 效果追踪），将练习作答写入 `StudentAnswer` 并自动触发障碍诊断与复习任务同步，供学生端练习页与教师端效果追踪消费（设计文档 28 §7）。

## Requirements

### Requirement: 练习任务列表

系统 SHALL 提供 `GET /api/practice/student/{uid}/tasks` 端点：返回该学生的练习任务列表，每项含 `practice_id`、`title`、`knowledge_points`、`difficulty`、`status`（pending/completed/expired）、`question_count`、`deadline`，并返回 `pending_count` 与 `completed_count`。student 角色 SHALL 仅可查询自己的任务，跨学生访问返回 403。

#### Scenario: 学生查询自身任务

- **WHEN** 学生查询自己的练习任务
- **THEN** 返回其 pending/completed 分组列表与两类计数

#### Scenario: 学生跨学生访问受限

- **WHEN** student 角色请求其他学生的任务列表
- **THEN** 返回 403 禁止访问

### Requirement: 练习提交与批改

系统 SHALL 提供 `POST /api/practice/submit` 端点：请求含 `practice_id` 与 `answers`（question_id + selected_option）；系统 SHALL 校验该练习记录归属请求学生，逐题判定对错并写入 `StudentAnswer`（含作答时间戳 `answered_at`），返回 `score`、`total`、`accuracy` 与逐题结果（题号 / 是否答对 / 正确答案 / 解析）。不存在的练习或非本人练习 SHALL 返回错误，不写入任何作答。

#### Scenario: 正常提交批改

- **WHEN** 学生提交一份完整练习答案
- **THEN** 系统逐题判定并写入作答记录，返回得分、总题数、正确率与逐题判定

#### Scenario: 提交他人练习被拒

- **WHEN** 提交的 practice_id 不属于请求学生
- **THEN** 返回业务错误，且不写入任何作答记录

### Requirement: 练习效果追踪

系统 SHALL 提供 `GET /api/practice/effect/{student_id}` 端点：取该学生最近两次练习记录，分别计算每次练习的正确率（答对数/总题数），返回 `student_id`、`student_name` 与 `improvement` 对象（前次练习日期与正确率、本次练习日期与正确率、进步率 = 本次正确率 - 前次正确率）。不足两次练习记录时 SHALL 返回空或缺失标记，不产生除零。

#### Scenario: 两次练习对比

- **WHEN** 学生已完成最近两次练习
- **THEN** 返回前次/本次正确率与进步率（本次减前次）

#### Scenario: 不足两次练习

- **WHEN** 学生仅有一次或没有练习记录
- **THEN** 返回空 improvement 且不报除零错误

### Requirement: 提交后自动触发诊断与复习同步

系统 SHALL 在练习提交批改落库后，通过后台任务自动触发两件事：① 障碍诊断——对该学生的作答执行诊断（无 LLM 可用时降级走规则引擎与融合单路，不阻塞提交响应）；② 复习任务同步——遍历本次错误作答，为尚未存在复习任务的学生-题目组合自动创建复习任务（同一学生同一题目去重，已存在不重复创建）。触发 SHALL 异步执行，不阻塞提交接口返回。

#### Scenario: 提交后自动创建复习任务

- **WHEN** 学生提交练习且其中某题答错、该题尚无复习任务
- **THEN** 系统自动创建该学生该题的复习任务（待复习状态，创建即到期）

#### Scenario: 重复同步去重

- **WHEN** 同一学生同一道错题被多次提交
- **THEN** 只存在一个复习任务，不重复创建

#### Scenario: 诊断降级不阻塞提交

- **WHEN** LLM 传输不可用而学生提交练习
- **THEN** 提交接口正常返回批改结果，诊断走规则与融合单路降级在后台执行
