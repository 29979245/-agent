## Purpose

提供每日练习调度器：APScheduler 每天 08:00 UTC 为已建档学生自动创建个性化每日练习、向绑定家长发送通知，并将到期未做的复习任务标记超期（调度层，设计文档 28 §6 与 29 §9）。

## Requirements

### Requirement: 每日练习创建

系统 SHALL 通过 APScheduler 每天 08:00 UTC 触发一次批量任务：遍历已建档学生，依据学生障碍画像映射知识点（`concept`/`reading`/`expression` 各对应一组知识点，画像缺失默认 `concept`），结合 ZPD 难度档位从真题库确定性抽样生成个性化练习题，为每位学生创建一份独立练习记录（per-student `ExamRecord`，类型为每日练习）。同一学生同一天 SHALL 至多创建一份，已创建则跳过。执行 SHALL 逐学生分批处理（每批最多 5 人），失败不阻断后续学生。

#### Scenario: 按障碍画像生成每日练习

- **WHEN** 每日调度触发且学生已建档
- **THEN** 按画像映射知识点、结合 ZPD 难度生成一份独立每日练习，同生同天不重复

#### Scenario: 画像缺失用默认映射

- **WHEN** 学生从未被诊断、障碍画像为空
- **THEN** 按 `concept` 默认映射生成每日练习

### Requirement: 家长每日通知

系统 SHALL 在每日练习创建成功后，向该学生绑定的家长（`Student.parent` 存在）发送练习已布置通知（含学生名与练习标题）。无绑定家长时 SHALL 静默跳过，不报错。

#### Scenario: 绑定家长收到通知

- **WHEN** 学生绑定家长且每日练习创建成功
- **THEN** 向该家长发送练习布置通知

#### Scenario: 无绑定家长静默跳过

- **WHEN** 学生未绑定家长
- **THEN** 跳过通知，不影响每日练习创建

### Requirement: 超期复习任务标记

系统 SHALL 在每日调度触发时，遍历该学生 `pending` 状态且 `next_review_at` 已过当前时刻的 `ReviewTask`，将其状态标记为 `overdue`。

#### Scenario: 到期未做置超期

- **WHEN** 每日调度触发且存在已过到期时间的 pending 任务
- **THEN** 该任务状态改为 `overdue`
