# OCR 批改结果落库边界：仅题库匹配模式展开写 StudentAnswer，而非动态建题

设计文档 24 §7.3 定义三种答案来源（题库匹配/教师录入/LLM自判），§7.4 批改结果以 `questions[]` 结构产出。但落库目标 `StudentAnswer.question_id` 是非空强外键（`exam.py`），诊断链路 `POST /api/diagnosis/run-llm` 按 `exam_id` 批跑、`barrier_type` 挂在题库题上。本阶段决策：**模式1（有考试、题库匹配）展开 `questions[]` 为 StudentAnswer 行并触发障碍诊断；模式2（教师录入）与模式3（LLM自判）只落 `StudentSubmission.answer_list`，不展开。**

**Why**：
1. **诊断/统计链路天然绑定考试+题库题**——自由用卷题目不在题库，展开后无 `question_id` 可挂、`barrier_type` 无处落，进不了诊断与班级统计。
2. **动态建题是脏题入口**——题库有完整审核链（Audit engine 状态机/重生成/批准入库），OCR 自用卷动态建题绕过审核，污染题库与统计。
3. **设计文档自身保留**——§7.3 模式3 原文标注"后续版本将接入 LLM 语义推断能力"，说明模式3 现阶段仅标记待确认，不进主链路。

**调和方式**：

| 答案来源 | 触发条件 | 落库 |
|---------|---------|------|
| 模式1 题库匹配 | 教师指定考试ID | StudentSubmission + 展开 StudentAnswer → 触发 run-llm 诊断 |
| 模式2 教师录入 | 无考试、教师逐题录入 | 只落 StudentSubmission.answer_list，教师人工复核 |
| 模式3 LLM自判 | 无考试、无录入 | 只落 StudentSubmission，标记待确认 |

**后果**：模式2/3 批改结果不进学情诊断链路；若该卷需进诊断，教师应先组卷入题库成考试（产品操作，非后端自动建题）。模式2 的 `answer_list` 保留完整题目结构，便于未来版本接入语义推断后补充展开。
