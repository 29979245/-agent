## Purpose

管理考试从创建到归档的完整生命周期：六态状态机（Draft→Published→InProgress→Grading→Completed→Archived）、题目双渠道关联、发布与完成统计、结果查询与级联删除。

## Requirements

### Requirement: 考试创建
系统 SHALL 提供考试创建接口，接受班级、名称、考试日期，创建处于 Draft 状态的考试记录，Draft 组卷期可增删题目。

#### Scenario: 创建草稿考试
- **WHEN** 教师提交 class_id、name、exam_date
- **THEN** 系统创建状态为 Draft 的考试并返回 record_id

### Requirement: 题目双渠道关联
系统 SHALL 支持向考试批量关联题目：渠道一按 Question 表直接关联已有题目；渠道二当题目不在 Question 表时，从真题库查找并将历史真题复制为 Question 实体后再关联。

#### Scenario: 关联题库已有题目
- **WHEN** 教师提交的 question_id 在 Question 表中存在
- **THEN** 该题目被关联到当前考试，无需新建

#### Scenario: 关联历史真题
- **WHEN** 教师提交的题目 ID 未在 Question 表但存在于真题库
- **THEN** 系统将真题复制为 Question 实体、写入题库后关联到考试

#### Scenario: 查看与移除考试题目
- **WHEN** 教师查看考试题目列表或移除其中一题
- **THEN** 列表返回当前全部关联题目；移除后该题解除关联

### Requirement: 考试发布
系统 SHALL 提供发布接口，发布前校验考试至少包含一道题目，发布时写入 question_stats（published=true、published_at、question_count）并统计 total_students。

#### Scenario: 发布通过
- **WHEN** 教师发布一道已含至少一题的考试
- **THEN** 考试状态进入 Published，question_stats 记录发布状态与时间

#### Scenario: 发布空考试被拒
- **WHEN** 教师发布不含任何题目的考试
- **THEN** 发布被拒绝并提示需先添加题目

### Requirement: 考试状态机
系统 SHALL 按六态状态机流转：Draft→Published→InProgress→Grading→Completed→Archived，非法转换被拒绝；InProgress 由首生作答触发、Grading 由教师触发、Completed 由 finalize 完成统计、Archived 为终态只读。

#### Scenario: 合法状态流转
- **WHEN** 考试依次经历 发布→作答→阅卷→finalize→归档
- **THEN** 状态依次变为 Published/InProgress/Grading/Completed/Archived

#### Scenario: 非法转换被拦截
- **WHEN** 尝试从 Draft 直接进入 Grading 或从 Published 直接归档
- **THEN** 转换被拒绝并返回状态机错误

#### Scenario: 归档终态只读
- **WHEN** 考试已归档
- **THEN** 不可再增删题、发布或取消归档，仅可查看与导出

### Requirement: 完成统计与结果查询
系统 SHALL 提供 finalize 接口统计参考人数与计算班级统计，并提供全班成绩总览与单学生逐题作答详情查询接口。

#### Scenario: finalize 完成统计
- **WHEN** 教师对 Grading 状态考试执行 finalize
- **THEN** 系统统计参考人数与班级统计，状态进入 Completed

#### Scenario: 结果总览
- **WHEN** 教师查询考试成绩总览
- **THEN** 系统返回全班学生成绩总览（含平均分/参考人数/题目数）

#### Scenario: 学生作答详情
- **WHEN** 教师查询某学生的作答详情
- **THEN** 系统返回该生逐题作答记录

### Requirement: 考试级联删除
系统 SHALL 提供考试删除接口，删除时级联删除该考试的作答记录与题目关联后删除考试记录；Draft 状态可删除，已发布考试删除受限。

#### Scenario: 级联删除草稿考试
- **WHEN** 教师删除 Draft 状态的考试
- **THEN** 作答记录与题目关联一并删除，考试记录移除

#### Scenario: 已发布考试删除受限
- **WHEN** 教师尝试删除已发布或之后的考试
- **THEN** 删除被拒绝或需更高权限确认

### Requirement: 考试列表查询
系统 SHALL 提供考试列表查询接口 `GET /api/exam`，分页返回考试概要（exam_id/name/class_name/question_count/status/exam_date），按 id 倒序（近似创建顺序），支撑前端考试列表 Tab。数据模型上考试归属班级而非教师，列表不做教师过滤。

#### Scenario: 分页列表
- **WHEN** 教师请求考试列表（page/page_size）
- **THEN** 返回分页结果，每项含考试概要字段

#### Scenario: 状态与统计字段
- **WHEN** 列表返回考试条目
- **THEN** 每条含当前状态（六态之一）、题目数量统计与考试日期

### Requirement: 班级信息查询
系统 SHALL 提供班级列表查询接口 `GET /api/classes`，返回班级概要（id/name），支撑考试创建表单的班级选择。

#### Scenario: 班级下拉
- **WHEN** 教师打开创建考试表单
- **THEN** 班级下拉数据来自 `GET /api/classes`
