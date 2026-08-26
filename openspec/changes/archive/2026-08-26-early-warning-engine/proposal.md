## Why

学情面板（class-learning-panel，已落地 `/api/panel`）只提供**聚合视图**，无法在数据异常时主动告警——教师需要人工逐班翻面板才发现"某学生 5 天没登录 / 成绩断崖下滑 / 错题率接近随机猜测"。预警引擎按产品设计文档 31《学情分析与预警系统设计》§六，把被动查看变为主动推送：定时扫描全部学生，命中 no_login / score_drop / high_error_rate 三类规则即创建 WarningLog 并推送家长通知，教师在 `/api/warning` 处理闭环。

## What Changes

- 新增 `WarningLog` 数据模型（doc 31 §5.4，13 字段：学生/类型/级别/标题/内容/结构化指标/处理状态/处理信息/通知标记/时间戳）+ 枚举 `WarningType` / `WarningLevel` / `WarningStatus`。
- 新增 `EarlyWarningService`（doc 31 §5.1）：
  - `check_all_warnings()` 遍历全部学生，依次检测三类规则，命中创建 pending 预警；
  - `no_login`：`(now − last_exercise_at).days >= 3`（`last_exercise_at` 由 `max(answered_at)` 推导，从未作答则用 `created_at`），固定 `warning`；
  - `score_drop`：仅正式考试（exam 类型），最近两次考试正确率 `(前次−最近)/前次 >= 10%` → `warning`，`>= 20%` → `critical`；
  - `high_error_rate`：最近一次考试/练习作答 `错误数/总题数 >= 50%` → `info`，`>= 70%` → `warning`；
  - **去重**：同 student_id + warning_type 已有 pending 不重复创建；
  - **通知**：向该学生全部活跃绑定家长（`student_parent_binding.status=active`）推送 `ParentNotification`（type=warning），无绑定静默跳过；教师经 `/api/warning/pending` 主动查看。
- 新增 `/api/warning` 五端点（doc 31 §6.2）：pending / student 历史 / process 处理 / check 手动触发 / class summary。
- `Student` 增加 `created_at` 字段（no_login 从未作答分支依赖）+ Alembic 迁移（既有行回填）。
- APScheduler 在既有 `create_scheduler()` 中追加预警检查任务（Cron 00:00 UTC，复用独立会话模式）。
- 权限矩阵新增 `warning` 资源：teacher/dept_admin/admin 可读+写，subject_lead 仅读，student 无任何 warning 权限；班级范围沿用 school_id 组织链隔离。
- 新障碍迁移（`new_barrier`）本轮延后（grilling 决策）。

## Capabilities

### New Capabilities
- `early-warning`: 预警引擎——WarningLog 模型、三类检测规则（no_login/score_drop/high_error_rate）、去重与通知管道、`/api/warning` 五端点、00:00 UTC 调度任务。

### Modified Capabilities
<!-- 无既有 spec 的 REQUIREMENTS 发生变化：data-models spec 仅约束账户模型，未枚举 Student 字段，
     Student.created_at 属实现层数据字段补充，不构成 spec 级行为变更。 -->

## Impact

- 后端新文件：`app/db/models/warning.py`（WarningLog）、`app/services/analytics/early_warning.py`（EarlyWarningService）、`app/api/v1/warning.py`（五端点）。
- 后端修改：`app/db/models/enums.py`（WarningType/Level/Status 枚举）、`app/db/models/org.py`（Student.created_at）、`app/core/permissions.py`（warning 资源矩阵）、`app/services/exercise/scheduler.py`（追加 00:00 UTC 预警任务）、`app/main.py`（注册 warning 路由）、Alembic 迁移。
- 数据源：`Student`（created_at）、`StudentAnswer`（answered_at / is_correct）、`ExamRecord`（exam_type / status / exam_date）、`StudentParentBinding`（status=active）、`ParentNotification`（type=warning）。
- 复用：`services/exercise/scheduler.py` 的独立会话模式、`daily.py` 的家长通知模式、school_id 组织链隔离与 `_require_class_in_teacher_school` 模式。
- 无新第三方依赖。
- 测试：`tests/unit/`（检测规则纯函数/去重/通知）+ `tests/integration/`（API 端点、权限、调度任务）。
