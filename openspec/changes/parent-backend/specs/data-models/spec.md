## MODIFIED Requirements

### Requirement: 家长与亲子绑定
系统 SHALL 提供家长、亲子绑定、家长通知三个实体。家长通过学生生成的 6 位绑定码与学生建立绑定；绑定状态记录于亲子绑定关系；通知按类型记录并追踪已读状态。家长通知 SHALL 记录创建时间以支持按时间倒序分页。通知类型枚举 SHALL 取值为 `weekly_report`（周报生成）、`score_alert`（成绩预警）、`learning_plan`（学习计划）、`reminder`（学习提醒）、`daily_report`（每日练习报告）五类。学生 SHALL 记录当周生成的周报 JSON（`summary`/`detail`/`advice`/`no_data`）与其归属周的起始日期，支撑同一周内每学生一份周报的去重。

#### Scenario: 绑定码建立亲子关系
- **WHEN** 家长提交学生的 6 位绑定码且匹配
- **THEN** 系统创建一条生效的亲子绑定记录，家长可查看该学生的数据

#### Scenario: 无效绑定码
- **WHEN** 家长提交的绑定码不匹配
- **THEN** 绑定失败，不创建绑定记录，并返回业务规则冲突错误

#### Scenario: 通知类型枚举取值
- **WHEN** 写入一条家长通知
- **THEN** 通知类型只能是 `weekly_report`/`score_alert`/`learning_plan`/`reminder`/`daily_report` 之一

#### Scenario: 通知带创建时间
- **WHEN** 创建一条家长通知
- **THEN** 通知记录创建时间，可按时间倒序排序

#### Scenario: 周报按周去重
- **WHEN** 学生当周已生成周报
- **THEN** 学生记录中保存该周起始日期与周报 JSON，同一周再次生成时判定为已存在
