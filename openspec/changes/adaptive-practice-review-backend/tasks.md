## 1. 模型扩展与迁移

- [x] 1.1 `student_answer` 增加 `answered_at` 时间戳列，编写 Alembic 迁移，验证 `pytest tests/` 迁移后可建表且默认值正确
- [x] 1.2 `review_task` 增加 `next_review_at`/`first_studied_at`/`completed_at`/`consecutive_correct`/`consecutive_error` 字段，编写迁移，验证字段可读写
- [x] 1.3 `ReviewTaskStatus` 重构为 `pending`/`overdue`/`done` 三态，编写迁移将旧四态映射（completed/archived→done，in_progress→pending），验证旧数据迁移后枚举合法
- [x] 1.4 `ReviewLevel` 校验 level1..level6 与 1/3/7/14/30 天/不再安排映射一致，编写单元测试验证间隔映射表

## 2. 自适应出题引擎（adaptive-practice-engine 服务层）

- [x] 2.1 实现 ZPD 难度计算（最近 30 条作答、<40% easy / 40%-70% medium 含两端 / >70% hard / 冷启动 medium），编写 L1 单元测试覆盖边界值 40%/70% 与冷启动
- [x] 2.2 实现薄弱知识点提取（全部错误作答、逐点累加、Top N 默认 3、一题多知识点逐点计数、无错题返回空），编写 L1 单元测试覆盖一题多 KP 与空记录
- [x] 2.3 实现主导障碍识别（`barrier_profile` 占比最高键，缺失/异常默认 `concept`），编写 L1 单元测试覆盖画像存在/缺失/异常
- [x] 2.4 实现策略矩阵与题库抽样出题（ZPD 档 + 薄弱 KP + 主导障碍策略 → 真题库复制抽样 `source=practice`，排除指定题，抽样不足标记不足），编写 L2 集成测试验证按知识点与难度抽样
- [x] 2.5 实现批次限制（单批 ≤5 学生，超 5 提示分批），编写 L1 单元测试覆盖 6 人输入截断提示

## 3. 间隔复习引擎（review 服务层）

- [x] 3.1 实现 `SpacedRepetitionEngine` 等级间隔映射（level1..level6 → 1/3/7/14/30/不再安排，首级当天 `next_review_at=now`），编写 L1 单元测试
- [x] 3.2 实现升降级状态机（先判正误：连续答对 2 次升级、答错回落豁免→保底→降级、level6 置 done、重算 `next_review_at = 提交时刻 + 新间隔`），编写 L1 单元测试覆盖六种判定路径
- [x] 3.3 实现复习任务到期查询（student 维度、按到期升序、pending/overdue、不含 done），编写 L2 集成测试
- [x] 3.4 实现复习提交（校验任务归属、判级、写 `ReviewHistory`、更新 `ReviewTask`、不写 `StudentAnswer`），编写 L2 集成测试验证只写 ReviewHistory

## 4. 错题强化（wrong-question 服务层）

- [x] 4.1 实现错题列表（按最近作答倒序去重、累计答错次数、无错题返回空），编写 L2 集成测试
- [x] 4.2 实现变式题生成（同知识点同难度抽样、排除原题、抽样不足标记不足），编写 L2 集成测试
- [x] 4.3 实现训练会话（per-student ExamRecord 训练记录、逐题批改写 StudentAnswer、答错同步复习任务），编写 L2 集成测试
- [x] 4.4 实现标记已掌握（校验错题归属、置 ReviewTask done、从错题列表移除，非本人 403），编写 L2 集成测试

## 5. 练习 API（practice-api）

- [x] 5.1 实现 `GET /api/practice/student/{uid}/tasks`（pending/completed 分组 + 计数，student 仅可查自己，跨学生 403），编写 L2 集成测试
- [x] 5.2 实现 `POST /api/practice/submit`（校验练习归属、逐题判定、写 StudentAnswer 含 answered_at、返回 score/total/accuracy/逐题结果，他人练习拒绝且不落库），编写 L2 集成测试
- [x] 5.3 实现 `GET /api/practice/effect/{student_id}`（最近两次正确率对比 + 进步率，不足两次返回空 improvement 不除零），编写 L2 集成测试
- [x] 5.4 实现提交后副作用（BackgroundTasks 异步触发诊断降级 + 复习同步去重，提交响应不阻塞），编写 L2 集成测试验证去重建 ReviewTask 且响应先行返回

## 6. 复习 API

- [x] 6.1 实现 `GET /api/review/tasks/{student_id}`（到期列表 + 权限），编写 L2 集成测试
- [x] 6.2 实现 `POST /api/review/submit`（提交判级、三态流转、归属校验），编写 L2 集成测试

## 7. 错题强化 API

- [x] 7.1 实现 `GET /api/wrong-questions/{student_id}` 错题列表端点，编写 L2 集成测试
- [x] 7.2 实现 `POST /api/wrong-questions/variants` 变式题端点，编写 L2 集成测试
- [x] 7.3 实现 `POST /api/wrong-questions/train` 训练会话端点，编写 L2 集成测试
- [x] 7.4 实现 `POST /api/wrong-questions/{question_id}/mastered` 标记已掌握端点，编写 L2 集成测试

## 8. 每日练习调度（daily-practice）

- [x] 8.1 实现每日练习创建（08:00 UTC 批量、画像映射知识点、ZPD 抽样、per-student ExamRecord、同生同天去重、分批 ≤5 且失败不阻断），编写 L1/L2 测试
- [x] 8.2 实现家长通知（绑定家长发通知、无绑定静默跳过），编写 L1 单元测试
- [x] 8.3 实现超期标记（pending 且过 `next_review_at` → overdue），编写 L1 单元测试

## 9. 接线与集成验证

- [x] 9.1 注册 practice/review/wrong_question 路由与 APScheduler 到 `main.py`，编写冒烟测试验证应用可启动且路由可达
- [x] 9.2 全量回归 `pytest tests/ --tb=short -x` 通过且既有 exam/diagnosis 测试无回归
- [x] 9.3 端到端验证闭环：出题 → 提交 → 自动诊断降级 → 复习同步 → 错题列表 → 变式 → 训练 → 每日调度，编写一条集成测试走通全链路
