## Purpose

提供错题间隔复习引擎的 REST 接口与状态机：按艾宾浩斯 6 级（1/3/7/14/30 天/不再安排）安排 `ReviewTask` 复习节奏，三态流转（待复习/超期/已掌握），复习提交判级并只写 `ReviewHistory`（不写 `StudentAnswer`），供学生端复习页消费（服务层 + API，设计文档 29）。

## Requirements

### Requirement: 复习任务状态三态与到期判定

系统 SHALL 维护 `ReviewTask` 三态：`pending`（待复习）/ `overdue`（超期）/ `done`（已掌握，终态）。任务创建时 SHALL 置 `pending` 并计算 `next_review_at = 创建时刻 + 间隔`（首级间隔按当天处理，即创建即到期）。到期未做的任务 SHALL 由每日调度器标记为 `overdue`。`done` 态任务 SHALL 不再参与到期查询与升降级。

#### Scenario: 创建即到期

- **WHEN** 提交练习后为新错题创建复习任务（首级）
- **THEN** 任务状态为 pending，`next_review_at` 为创建时刻（当天即到期）

#### Scenario: 到期未做标记超期

- **WHEN** 每日调度器发现某 pending 任务已过 `next_review_at`
- **THEN** 该任务状态改为 `overdue`

### Requirement: 复习任务到期查询

系统 SHALL 提供 `GET /api/review/tasks/{student_id}` 端点：返回该学生按到期时间排序的待复习任务列表（含 `pending` 与 `overdue`，不含 `done`），每项含 `review_task_id`、`question_id`、`review_level`、`status`、`next_review_at` 与题目内容。student 角色 SHALL 仅可查询自己的任务，跨学生访问返回 403。

#### Scenario: 学生查询自身待复习任务

- **WHEN** 学生查询自己的复习任务
- **THEN** 返回 pending/overdue 任务列表，按到期时间升序

#### Scenario: 学生跨学生访问受限

- **WHEN** student 角色请求其他学生的复习任务
- **THEN** 返回 403 禁止访问

### Requirement: 复习提交与升降级

系统 SHALL 提供 `POST /api/review/submit` 端点：请求含 `review_task_id` 与 `passed`（本次是否复习正确）。系统 SHALL 先判定正误再执行升降级：答对时连续答对计数 +1，连续答对达 2 次 SHALL 升级（`level` < 6 时），不足 2 次保持当前级；答错时 SHALL 按「回落豁免 → 保底 → 降级」顺序处理——若上次已连续答对 1 次（本次首次答错）则回落不降级，若当前为 level1 则保底不降级，其余情况降 1 级。每次提交 SHALL 写入一条 `ReviewHistory`（含复习后等级与正误），并按新等级重算 `next_review_at = 提交时刻 + 新间隔`；等级升至 level6 SHALL 将任务置为 `done`（终态）。复习结果 SHALL 只写 `ReviewHistory` 与 `ReviewTask`，不写 `StudentAnswer`。

#### Scenario: 连续答对两次升级

- **WHEN** 学生连续两次提交同一复习任务且均答对
- **THEN** 第二次提交后复习等级升 1 级，重算 `next_review_at`

#### Scenario: 答错一次回落豁免

- **WHEN** 学生上次答对（连续答对计数为 1）而本次答错
- **THEN** 回落豁免，等级不降级，连续答对计数清零

#### Scenario: 首级答错保底

- **WHEN** 学生当前为 level1 且答错
- **THEN** 保底不降级，仅清零连续计数

#### Scenario: 升至最高级标记已掌握

- **WHEN** 学生答对使复习等级升至 level6
- **THEN** 任务状态置 `done`，不再进入到期查询

#### Scenario: 复习不写学生作答

- **WHEN** 学生提交一次复习结果
- **THEN** 仅写入 `ReviewHistory` 并更新 `ReviewTask`，不产生任何 `StudentAnswer`
