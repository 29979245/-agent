## Why

Phase 3（v0.3.0）已完成诊断引擎、题库与出题工作台，但"诊断-干预"闭环的"行动"环节缺失：`services/exercise/` 是空目录，全库无一处写入 `StudentAnswer`，也没有练习提交、自适应出题、间隔复习、错题强化或每日推送。依据设计文档 28（自适应练习引擎）与 29（间隔复习与错题强化训练系统），本轮实现两个引擎的后端 API，打通「诊断 → 出题 → 练习 → 复习 → 反馈」的闭环。

## What Changes

- **作答管线（新）**：`POST /api/practice/submit` 批改并写入 `StudentAnswer`（新增 `answered_at` 时间戳），提交后自动触发异步障碍诊断与复习任务同步（答错去重建 ReviewTask）
- **自适应出题引擎（新）**：ZPD 难度计算（30 题窗口，<40% easy / 40%-70% medium / >70% hard，两端临界归 medium）、薄弱知识点 Top3、主导障碍识别、策略矩阵；本轮出题走确定性**题库抽样**（薄弱 KP + ZPD 难度），LLM 生成留待 v1.1
- **练习 API（新）**：任务列表 / 提交 / 效果追踪（`GET /api/practice/student/{uid}/tasks`、`POST /api/practice/submit`、`GET /api/practice/effect/{student_id}`）
- **间隔复习引擎（新）**：`ReviewTask` 状态三态（pending/overdue/done）、艾宾浩斯 6 级升降级（答对连续 2 次升级 / 答错回落豁免+保底+降级）、到期查询、复习提交；复习结果只写 `ReviewHistory`，不写 `StudentAnswer`
- **错题强化（新）**：错题列表 / 变式题生成（题库抽样近似）/ 训练会话 / 标记已掌握
- **每日推送调度器（新）**：APScheduler 每天 08:00 UTC 生成每日练习（按障碍画像映射知识点）、向绑定家长发通知、将到期未做的复习任务标记 overdue
- **模型扩展**：`ReviewTask` 增补 `next_review_at`/`first_studied_at`/`completed_at`/`consecutive_correct`/`consecutive_error`；`ReviewTaskStatus` 重构为三态；`StudentAnswer` 新增 `answered_at`
- **本轮不做** Agent 工具集成（`assign_adaptive_practice` 预览版留到 Agent 域）

## Capabilities

### New Capabilities
- `adaptive-practice-engine`: ZPD 难度计算、薄弱知识点 Top3、主导障碍识别、策略矩阵、题库抽样出题（服务层）
- `practice-api`: 练习任务/提交/效果追踪 API、作答管线（StudentAnswer 写入）、submit 副作用（自动触发诊断与复习同步）
- `review`: 间隔复习引擎（ReviewTask 三态、6 级升降级、到期查询、复习提交、答错自动同步）
- `wrong-question`: 错题强化（错题列表、变式题生成、训练会话、标记已掌握）
- `daily-practice`: 每日练习调度（APScheduler 接线、每日练习创建、家长通知、超期标记）

### Modified Capabilities
<!-- 无既有规格的需求变更；练习记录复用 ExamRecord（exam-lifecycle）不改变其教师侧六态行为 -->

## Impact

- **模型**：`app/db/models/review.py`（ReviewTask 字段扩展）、`app/db/models/enums.py`（ReviewTaskStatus 三态）、`app/db/models/exam.py`（StudentAnswer.answered_at）；配套 Alembic 迁移
- **服务**：`app/services/exercise/`（ZPD、AdaptivePracticeService、SpacedRepetitionEngine、WrongQuestionTrainer、每日调度器）
- **API**：`app/api/v1/` 新增 `practice.py`、`review.py`；`app/main.py` 注册路由与 APScheduler
- **依赖**：APScheduler 已在 requirements.txt，本轮接线
- **约束**：出题/变式题走确定性题库抽样，LLM 传输仍为桩（不阻塞闭环）；本轮不做 Agent 工具集成
