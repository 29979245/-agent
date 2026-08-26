## 1. 数据模型与迁移

- [x] 1.1 实现 `WarningLog` 模型（id/student_id/warning_type/level/title/content/data JSON/status/processed_by/processed_at/processed_note/notified_* /created_at）与 `WarningType`/`WarningLevel`/`WarningStatus` 枚举，编写 L1 单元测试覆盖模型字段与默认值
- [x] 1.2 为 `Student` 增加 `created_at`（default=utcnow）并编写 Alembic 迁移（加列 + 既有行回填 COALESCE(max(answered_at), now)），编写 L1 单元测试覆盖回填与 NULL 兜底

## 2. EarlyWarningService 检测规则

- [x] 2.1 实现 `detect_no_login` 纯函数（last_exercise_at 由 max(answered_at) 推导、从未作答用 created_at、3 天阈值、created_at None 跳过），编写 L1 单元测试覆盖曾作答/从未作答/未满阈值/created_at None
- [x] 2.2 实现 `detect_score_drop` 纯函数（仅 exam 类型、最近两次考试正确率、drop>=0.1 warning / >=0.2 critical、prev=0 或不足两场不触发），编写 L1 单元测试覆盖分级与边界
- [x] 2.3 实现 `detect_high_error_rate` 纯函数（最近一场考试/练习错题率，>=0.7 warning / >=0.5 info、无作答不触发），编写 L1 单元测试覆盖分级与边界
- [x] 2.4 实现 `EarlyWarningService.check_all_warnings`（遍历全部学生、三规则命中 → 创建/去重/通知、分批 flush、单生失败不阻断、返回 summary），编写 L1 单元测试覆盖批量与单生异常

## 3. 去重与通知

- [x] 3.1 实现创建前去重（同 student_id + warning_type + pending 不重复创建，不同类型互不影响），编写 L1 单元测试覆盖已存在跳过与新类型放行
- [x] 3.2 实现家长通知管道（active 绑定家长各推一条 ParentNotification type=warning、无绑定静默跳过、回写 notified_parent/notified_teacher、notified_student 预留），编写 L1 单元测试覆盖有/无绑定两种场景

## 4. /api/warning 端点

- [x] 4.1 实现 `GET /api/warning/pending`（可选 class_id 筛选、触发时间倒序、join 学生姓名与班级名），编写 L2 集成测试覆盖有数据与空列表
- [x] 4.2 实现 `GET /api/warning/student/{student_id}` 学生预警历史（含已处理/已忽略、倒序），编写 L2 集成测试
- [x] 4.3 实现 `PUT /api/warning/{warning_id}/process`（action=processed/ignored + note，记录处理人/时间/备注），编写 L2 集成测试覆盖两种 action 与不存在的 warning 404
- [x] 4.4 实现 `POST /api/warning/check` 手动触发全量检查（返回 created/by_type/failed），编写 L2 集成测试覆盖触发后创建预警
- [x] 4.5 实现 `GET /api/warning/class/{class_id}/summary`（total/by_type/by_level/critical_count），编写 L2 集成测试

## 5. 权限与隔离

- [x] 5.1 权限矩阵新增 `warning` 资源（admin/dept_admin 全量、teacher read/update/create、subject_lead read、student 无），编写 L1 单元测试覆盖各角色判定
- [x] 5.2 端点权限与隔离（student 访问任意 warning 端点 403；教师访问他校学生/班级预警 403、本校放行），编写 L2 集成测试

## 6. 调度与接线

- [x] 6.1 在 `scheduler.py` 追加 00:00 UTC 预警任务（独立会话 → check_all_warnings，仿 run_daily_job），编写冒烟测试验证任务注册与手动执行
- [x] 6.2 在 `main.py` 注册 warning 路由，全量回归 `pytest tests/ --tb=short -x` 通过，既有 exam/diagnosis/exercise 测试无回归
- [x] 6.3 运行 `graphify update .` 保持知识图谱同步
