## Context

预警引擎消费的学情数据已就绪：`StudentAnswer`（answered_at / is_correct）、`ExamRecord`（exam_type / status / exam_date）、`StudentParentBinding`（status=active）、`ParentNotification`（type=warning）。学情面板（class-learning-panel）已落地 `/api/panel` 聚合出口，本 change 在其上增加**主动告警**层。三个现状约束塑造本设计：

- **Student 无 `created_at`**——no_login 从未作答分支（`(now - created_at).days >= 3`）依赖该字段，需加字段 + 迁移回填。
- **无任教关系表**——班级/学生隔离沿用 school_id 组织链（复用面板 `_require_class_in_teacher_school` 模式）。
- **权限矩阵无 warning 资源**——预警端点需要读+写，但 teacher 仅 `analysis:read`；新增 `warning` 资源最干净（详见 D7）。

设计范围：三类检测规则（no_login / score_drop / high_error_rate），`new_barrier` 延后（grilling 决策）。

## Goals / Non-Goals

**Goals:**
- `EarlyWarningService`：`check_all_warnings()` 遍历全部学生，命中规则创建 `WarningLog`，去重 + 家长通知。
- `/api/warning` 五端点：pending / student 历史 / process / check / class summary。
- `Student.created_at` 字段 + 迁移回填。
- APScheduler 每日 00:00 UTC 预警任务（复用独立会话模式）。
- 权限矩阵新增 `warning` 资源，student 拒绝。

**Non-Goals:**
- `new_barrier` 新障碍迁移检测（延后独立 change）。
- 预警处理的外部反馈闭环（教师标记处理后回写学情模型，如解除冻结）——本次仅记录处理状态。
- 学生端通知推送（仅预留 `notified_student` 标记）。
- 预警阈值按班级/年级配置化（本期类常量，后续可独立 change）。

## Decisions

### D1. WarningLog 数据模型 + 枚举

新增 `app/db/models/warning.py`，枚举 `WarningType`（no_login / score_drop / high_error_rate）、`WarningLevel`（info / warning / critical）、`WarningStatus`（pending / processed / ignored）放入既有 `enums.py`。WarningLog 字段对应 doc 31 §5.4 的 13 字段：id、student_id（FK）、warning_type、level、title、content、data（JSON，结构化指标：days / drop_rate / error_rate）、status、processed_by（int 可空）、processed_at（可空）、processed_note（可空）、notified_teacher / notified_parent / notified_student（bool，student 预留）、created_at。

**为何独立模型文件**：与 `org.py`/`exam.py` 的模型分类一致，预警是独立领域实体，避免塞进 org。

### D2. Student.created_at + 迁移回填

`Student` 增加 `created_at`（`DateTime, default=datetime.utcnow`）。Alembic 迁移：加列 + **回填既有学生** `created_at = COALESCE(max(answered_at), now)`（每生取其最近作答时间，无作答取迁移时刻）。

**为何回填到最近作答而非建库时间**：若回填为老时间，存量学生上线即被 no_login 批量误报；回填到最近作答使"从未作答的学生"才能触发从未登录分支，与行为一致。`created_at` 理论上迁移后非空，但防御性保留 NULL → 跳过 no_login。

### D3. EarlyWarningService：检测规则纯函数 + 组装

`app/services/analytics/early_warning.py`。检测逻辑拆成纯函数（L1 可单测，无 DB）：

- `detect_no_login(last_exercise_at, created_at, now, threshold=3) -> "warning" | None`——last_exercise_at 非空用 `(now - last).days >= 3`；空则用 `(now - created).days >= 3`（created 为 None 返回 None）。
- `detect_score_drop(points, threshold=0.1, critical=0.2) -> "critical"|"warning"|None`——points 为升序 `(date, accuracy)`，取最近两场；`drop = (prev - recent)/prev`（prev<=0 返回 None）。
- `detect_high_error_rate(error_rate, info=0.5, warning=0.7) -> "warning"|"info"|None`。

组装类 `EarlyWarningService(db)`：
- `check_all_warnings(now=None)`——遍历全部学生，对每生执行三类检测 → `_create_warning`（去重）→ `_send_warning_notifications`；分批 flush（≤50，仿 daily `BATCH_LIMIT`），单学生异常不阻断；返回 summary（total / created / by_type / notified / failed）。
- `check_student(student, now=None)`——单学生（供测试与未来按需）。
- `_last_exercise_at(student)`——`max(StudentAnswer.answered_at)` 推导。
- `_recent_exam_points(student)`——`exam_type=exam` 且 published/completed，按 exam_date 升序，逐场算正确率（复用面板同款口径，查询作用域是单学生）。
- `_last_exam_error_rate(student)`——该学生最近一场 published/completed 考试/练习的 `错误数/总题数`。

**阈值类常量**：`NO_LOGIN_DAYS=3`、`SCORE_DROP_THRESHOLD=0.1`、`SCORE_DROP_CRITICAL=0.2`、`HIGH_ERROR_RATE_INFO=0.5`、`HIGH_ERROR_RATE_WARNING=0.7`（doc 31 §5.3 依据：3 天=周末+1 工作日缓冲；0.1≈一个标准差；0.5≈随机猜测线上方的系统性失败线）。

### D4. 去重

`_create_warning` 前查 `WarningLog(student_id, warning_type, status=pending)`，存在即跳过（spec「预警去重」）。与每日练习"同生同天去重"同一模式，幂等保证定时与手动触发安全。

### D5. 通知管道

`_send_warning_notifications(warning, student)`：查 `StudentParentBinding(student_id, status=active)`，为每个活跃绑定 `ParentNotification(type=warning, title=预警标题, content=学生姓名+预警摘要)`；无绑定返回 0。写回 `warning.notified_parent`；`notified_teacher=True`（教师经 `/api/warning/pending` 主动查看，doc 31 §6.3）；`notified_student=False` 预留。

### D6. `/api/warning` 五端点

`app/api/v1/warning.py`，`prefix="/api/warning"`。权限：`@require_permission("warning", <action>)` + `_ensure_not_student`。隔离复用面板模式：
- `GET /pending?class_id=`——`warning:read`；带 class_id 时 `_require_class_in_teacher_school`；返回 join 学生姓名/班级名，触发时间倒序。
- `GET /student/{student_id}`——`warning:read`；教师仅本校学生（沿用诊断 `_require_student_in_teacher_school` 组织链），返回历史。
- `PUT /{warning_id}/process`——`warning:update`；body `{action: processed|ignored, note}`；置状态 + processed_by/at/note。
- `POST /check`——`warning:create`；调 `check_all_warnings()`，返回 `{created, by_type, failed}`。
- `GET /class/{class_id}/summary`——`warning:read` + `_require_class_in_teacher_school`；返回 `{class_id, class_name, total, by_type, by_level, critical_count}`。

### D7. 权限矩阵新增 warning 资源

`RESOURCES` 追加 `"warning"`。矩阵：admin/dept_admin 全量；subject_lead 沿用只读（`{r: {"read"}}`）；teacher 增 `{"read", "update", "create"}`；student 默认拒绝（矩阵无该项即 deny）。**为何新增资源而非复用 analysis**：teacher 对 analysis 仅 read，处理预警需要 update/create，硬套 analysis 会漏放行或越权；独立资源最清晰，且 student 天然无 warning 权限。

### D8. APScheduler 预警任务

`services/exercise/scheduler.py` 的 `create_scheduler()` 追加 `WARNING_CRON = CronTrigger(hour=0, minute=0, timezone="UTC")`，job 调 `run_warning_job()`（独立 `SessionLocal` → `EarlyWarningService(db).check_all_warnings()`，仿 `run_daily_job`）。`misfire_grace_time=3600` 兜底错过触发。本 change **只加预警任务**，不动既有 daily_practice。

## Risks / Trade-offs

- **[R1] no_login 依赖 created_at，存量学生无该字段** → 迁移回填到最近作答时间，避免上线即批量误报；NULL 防御性跳过。副作用：从未作答的存量学生回填为 now → 注册时间失真，需 3 天后再触发。缓解：可接受（从未作答本就是少数冷启动学生）。
- **[R2] score_drop 仅 exam，练习为主的学生序列稀疏** → 与面板均分口径统一（D3）；不足两场不触发，避免误报。
- **[R3] 全量遍历学生查询开销** → 单生查询量小（≤千级作答）；分批 flush + 单生失败不阻断；`last_exercise_at` 用 max 聚合一次，不做逐条排序。
- **[R4] 权限矩阵新增资源** → 同步补矩阵单测与集成测试（student 403）；admin/dept_admin 全量不受隔离限制。
- **[R5] 并行进程同改 permissions.py / main.py / scheduler.py** → apply 时先 `git status`/`git diff` 厘清归属，只 stage 本 change 文件；若冲突按"新增不改动既有行"原则合并。

## Migration Plan

1. Alembic 迁移：`student` 加 `created_at` + 回填既有行（见 D2）。
2. 新增 `WarningLog` 表（随 `Base.metadata` / 独立迁移，与既有表一致）。
3. 可独立部署：新增模块 + 路由注册 + 调度任务，不触碰既有行为。
4. 回滚：移除 warning 路由与调度任务，删除新增模块；`created_at` 列保留无害（面板/诊断不依赖）。

## Open Questions

无阻塞性问题。`new_barrier`（新障碍迁移检测）为明确延后项，不作为开放问题遗留。
