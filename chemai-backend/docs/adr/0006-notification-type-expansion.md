# ParentNotification 类型由 3 类扩展为 5 类（weekly_report/score_alert/learning_plan/reminder/daily_report）

设计文档 33 §九 定义了 5 种家长通知类型，但既有 `NotificationType` 枚举仅有 3 类（message/report/warning），无法承载周报、成绩预警、每日报告的分通道语义。本阶段决策：**枚举扩展为设计文档定义的 5 类，并做存量值迁移映射。**

**Why**：
1. **语义必须对齐设计 33 §九**——周报通知（weekly_report）、成绩预警（score_alert）、学习提醒（reminder）是家长端消息 Tab 的分类基础，3 类枚举无法区分。
2. **发送方既有代码依赖具体类型**——`daily.py` 发「今日练习已布置」（原 message）、`early_warning.py` 发预警（原 warning），映射后前端可按类型渲染图标/优先级，无需约定俗成。

**调和方式**：`app/db/models/enums.py::NotificationType` 定义为 5 值 `weekly_report/score_alert/learning_plan/reminder/daily_report`；存量迁移做两段映射——`message→daily_report`、`warning→score_alert`、`report→weekly_report`；发送方同步更新：`daily.py::notify_parent` 用 `daily_report`，`early_warning.py::_send_warning_notifications` 用 `score_alert`，周报生成用 `weekly_report`。

**后果**：旧通知记录在迁移后归入新语义类型，前端按 5 类渲染；新增通知类型只需沿用枚举值，无需改表结构。
