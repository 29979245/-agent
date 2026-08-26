## Purpose

提供错题强化训练能力：错题列表、变式题生成（题库抽样近似）、训练会话与标记已掌握，供学生端错题本页面消费（服务层 + API，设计文档 29 §5-§8）。出题走确定性题库抽样，LLM 生成留待 v1.1。

## ADDED Requirements

### Requirement: 错题列表

系统 SHALL 提供 `GET /api/wrong-questions/{student_id}` 端点：返回该学生的历史错题列表，按最近作答时间倒序去重（同一学生同一题只出现一次），每项含 `question_id`、题目内容、知识点、`error_count`（累计答错次数）与最近作答时间。无错题记录时 SHALL 返回空列表。

#### Scenario: 学生查询自身错题

- **WHEN** 学生查询自己的错题本
- **THEN** 返回按最近作答倒序、去重后的错题列表，含累计答错次数

#### Scenario: 无错题返回空

- **WHEN** 学生没有任何错误作答记录
- **THEN** 返回空列表

### Requirement: 变式题生成

系统 SHALL 提供 `POST /api/wrong-questions/variants` 端点：请求含 `question_id` 与 `count`（默认 1）；系统 SHALL 依据原题知识点与同难度档位从真题库检索并复制抽样题目作为变式题（`source=practice`），并排除原题自身。抽样不足时 SHALL 返回已抽样结果并标记不足，不阻断返回。

#### Scenario: 按知识点同难度抽变式

- **WHEN** 学生对一道错题请求生成变式题
- **THEN** 返回同知识点同难度的题目副本，来源标记为日常练习，且不含原题

#### Scenario: 抽样不足兜底

- **WHEN** 目标知识点变式题不足
- **THEN** 返回已抽样题目并标记抽样不足，不报错

### Requirement: 训练会话

系统 SHALL 提供 `POST /api/wrong-questions/train` 端点：请求含 `student_id` 与题目列表（`question_id` 列表）；系统 SHALL 为该学生创建一份独立练习记录（per-student `ExamRecord`，类型为训练），逐题作答批改写入 `StudentAnswer`，并返回逐题判定结果。训练结果 SHALL 触发复习任务同步（答错的题目创建/复用 `ReviewTask`）。

#### Scenario: 训练会话判分

- **WHEN** 学生发起一次错题训练并作答
- **THEN** 系统创建该学生的独立训练记录并逐题批改，返回逐题判定

#### Scenario: 训练答错同步复习

- **WHEN** 训练中某题答错
- **THEN** 自动为该题创建/复用该学生的复习任务

### Requirement: 标记已掌握

系统 SHALL 提供 `POST /api/wrong-questions/{question_id}/mastered` 端点：请求含 `student_id`；系统 SHALL 校验该题确属该学生的错题，将对应 `ReviewTask` 置为 `done`（已掌握终态）并从错题列表移除。非本人错题 SHALL 返回 403。

#### Scenario: 标记错题已掌握

- **WHEN** 学生确认某错题已掌握
- **THEN** 该题对应复习任务置 `done`，不再出现在错题列表与到期查询

#### Scenario: 非本人错题受限

- **WHEN** 学生尝试标记不属于自己的题目
- **THEN** 返回 403 禁止访问
