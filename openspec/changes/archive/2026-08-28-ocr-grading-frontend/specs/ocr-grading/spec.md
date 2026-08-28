## ADDED Requirements

### Requirement: 批改结果题型标注
批改判卷 API 返回的逐题判定 items SHALL 在题库匹配模式（有考试）下为每项注入 `has_options` 字段，标记该题是否有选项（有选项为选择题），供前端拆分选择题/填空题成绩列；无关联考试（教师录入/自判模式）时该字段 SHALL 可为空或缺失，前端不据此拆分。

#### Scenario: 有考试时注入题型标注
- **WHEN** 批改任务关联考试且题库存在对应题目
- **THEN** 每个逐题判定 item 携带 `has_options` 布尔值，与题目 options 一致

#### Scenario: 无考试时不拆分
- **WHEN** 批改任务无关联考试（教师录入或 LLM 自判模式）
- **THEN** 逐题 items 不强制携带 `has_options`，前端不拆分选择题/填空题列
