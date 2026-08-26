## Context

Phase 3（v0.3.0）已完成诊断引擎、真题库与出题工作台，但「诊断 → 出题 → 练习 → 复习 → 反馈」闭环的「行动」环节缺失：`services/exercise/` 为空目录，全库无一处写入 `StudentAnswer`。动机见 proposal.md - Why。

约束现状：
- `app/services/diagnosis/llm_diagnosis.py` 的 `_chat` 是桩（`DiagnosisLLMError("...待 LLM 基建")`），本轮 LLM 传输仍不可用，出题/变式题必须走确定性题库抽样，不阻塞闭环
- 题库基础设施已存在（`HistoricalBank`、ChromaDB 向量检索、`import_historical`、`split_knowledge_points()`、`question_dict()`）
- `ReviewTask` 模型极简（无时间字段/计数），`ReviewTaskStatus` 为四态，`StudentAnswer` 无作答时间戳——需模型扩展 + Alembic 迁移
- 数据库 SQLite WAL（开发），APScheduler 3.10.4 已在 requirements.txt 未接线
- 练习记录沿用 `ExamRecord`（exam-lifecycle），不改变其教师侧六态行为

## Goals / Non-Goals

**Goals:**
- 打通闭环后端：自适应出题（ZPD + 薄弱 KP + 主导障碍 + 策略矩阵）→ 练习提交批改 → 自动诊断与复习同步 → 间隔复习 → 错题强化 → 每日推送
- 所有出题/变式题/训练走确定性题库抽样，零 LLM 依赖，提交响应不被后台副作用阻塞
- 练习、训练、每日练习均按学生组织为独立 `ExamRecord`（per-student），不共享题目与记录

**Non-Goals:**
- LLM 传输接入（`_chat` 桩保留，诊断降级路径）
- Agent 工具集成（`assign_adaptive_practice` 预览版留到 Agent 域）
- 前端页面与题面渲染（本轮仅后端 API）
- 4 档 `competition` 难度分配（不参与 ZPD 自动分配）

## Decisions

### D1: 练习/训练/每日练习按学生组织为独立 ExamRecord

练习、错题训练、每日练习均创建 per-student 的 `ExamRecord`（`exam_type=practice`/`homework`），每位学生一份独立题目与独立记录，不共享同一份试卷。题面复制自真题库（`source=practice`），与教师端 exam 六态流程完全隔离。

**备选**：设计文档 §9.3 的共享练习记录。**否决**——学生进度/错题归属/权限边界（student 仅可查自己）在共享模型下无法成立，且会污染教师端 exam 语义。此决策与设计文档偏差记入 ADR。

### D2: 出题与变式题走确定性题库抽样

ZPD 难度档位 + 薄弱知识点 Top3 + 主导障碍确定策略后，从真题库检索并复制题目（含内容/选项/答案/解析/知识点/难度），排除调用方指定排除的题（如刚做错的原题），组装为练习/变式题。抽样不足标记不足、不阻断。

**备选**：LLM 生成。**否决**——`_chat` 桩未接入，且确定性抽样可测（L1/L2 单元与集成可断言）。LLM 生成留待 v1.1 替换抽样入口，接口签名不变。

### D3: 提交批改的副作用异步执行

`POST /api/practice/submit` 同步完成批改落库并返回结果；批改落库后通过 FastAPI `BackgroundTasks` 触发两件异步副作用：① 障碍诊断（LLM 不可用时降级走规则引擎与融合单路）；② 复习同步（遍历错误作答，为学生-题目组合去重建 `ReviewTask`，创建即到期）。响应不等待副作用完成。

**备选**：同步触发 / 消息队列。**否决**——同步会阻塞提交响应（违反 spec「不阻塞」）；引入 MQ 对本规模过度设计，`BackgroundTasks` + SQLite 已足够。

### D4: ReviewTask 三态 + 艾宾浩斯 6 级状态机

`ReviewTaskStatus` 重构为 `pending`/`overdue`/`done`（终态）。`review_level` 的 `level1..level6` 映射设计 0-5（1/3/7/14/30 天/不再安排）；首级「当天」不设独立枚举，用 `next_review_at = 创建时刻` 表达（创建即到期）。

升降级规则（先判正误再判升降级）：
- 答对：`consecutive_correct` 达 2 → 升级（level < 6）；否则保持
- 答错：回落豁免（上次已连续答对 1 次 → 不降级）→ 保底（level1 → 不降级）→ 否则降 1 级
- `next_review_at = 提交时刻 + 新等级间隔`（非上次复习时刻 + 间隔，依设计 §10）
- level6 达级 → `status=done`，不再参与到期查询

**备选**：设计 §4.2 的「答错即降级」。**否决**——与 §4.1 回落豁免冲突，用户裁定选 §4.1。

### D5: 复习结果只写 ReviewHistory

复习提交只追加 `ReviewHistory`（复习后等级/正误/时间）并更新 `ReviewTask`，绝不写 `StudentAnswer`，保证「复习数据」与「练习数据」两条管线语义隔离，避免复习正确率污染 ZPD 与错题统计。

### D6: ZPD 计算边界规则

取最近 30 条 `StudentAnswer`（按 `answered_at` 降序）算正确率：<40% → `easy`；40%-70%（含两端）→ `medium`；>70% → `hard`；冷启动（无记录）→ `medium`。40% 与 70% 两个临界值均归 `medium`。`competition` 不参与分配。

### D7: 障碍画像 → 策略矩阵 → 知识点映射

- 主导障碍 = `Student.barrier_profile` 中占比最高键（缺失/异常默认 `concept`）
- 策略矩阵：`concept` → 降低难度档 + 基础知识点；`reading`/`expression` → 保持 ZPD 难度
- 知识点映射：`concept`/`reading`/`expression` 各对应一组知识点（配置常量），供每日练习批量映射；出题优先用薄弱知识点 Top3，不足由调用方传入知识点补足

### D8: 调度接线

APScheduler 每天 08:00 UTC 触发每日批量任务，逐学生分批（每批 ≤5 人，超 5 提示分批），依次：按画像映射生成每日练习（同生同天不重复）→ 通知绑定家长（无绑定静默跳过）→ 将过期的 pending `ReviewTask` 标记 `overdue`。单学生失败不阻断后续。

## Risks / Trade-offs

- **[确定性抽样题量不足 / 同质化]** → 抽样不足时返回已抽样结果并标记不足；题目同质化由题库扩充缓解，不阻断闭环
- **[后台副作用与请求并发写库]** → SQLite WAL 支持并发读；副作用为新增行（ReviewTask/ReviewHistory），幂等去重建（唯一组合查询），冲突风险低
- **[BackgroundTasks 丢失风险]** → 进程崩溃时副作用不执行；通过重复提交去重 + 次日调度器兜底（每日创建/超期标记），可接受
- **[迁移兼容]** → `ReviewTaskStatus` 四态→三态、模型加列需 Alembic 迁移；旧数据 `completed`/`archived` 映射为 `done`，`in_progress` 映射为 `pending`
- **[ZPD 正确率受复习污染]** → 复习只写 ReviewHistory（D5）从源头隔离

## Migration Plan

1. `alembic revision`：`student_answer` 加 `answered_at`；`review_task` 加 `next_review_at`/`first_studied_at`/`completed_at`/`consecutive_correct`/`consecutive_error`；`review_task_status` 枚举迁移四态→三态（映射见 D8 迁移兼容）
2. 服务层落地顺序：`exercise/`（ZPD、AdaptivePracticeService、SpacedRepetitionEngine、WrongQuestionTrainer、每日调度器）→ API（practice.py、review.py、wrong_question.py、daily_practice.py）→ `main.py` 注册路由与 APScheduler
3. 回滚：数据库恢复迁移前备份；代码按提交原子化回退。出题/复习均为新增能力，不破坏既有 exam/diagnosis 行为
