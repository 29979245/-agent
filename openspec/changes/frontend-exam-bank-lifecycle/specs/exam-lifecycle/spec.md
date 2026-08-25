## ADDED Requirements

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
