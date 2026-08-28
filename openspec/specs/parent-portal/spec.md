## Purpose

家长端后端门户：通过绑定码建立家长与子女的授权关系，以"仅见绑定子女"的数据权限边界提供子女报告、周报、AI 通俗解读与系统通知服务，让家长用通俗语言了解子女化学学习状态。

## Requirements

### Requirement: 家长绑定学生
系统 SHALL 提供 `POST /api/parent/bind`，接受学生 ID、绑定码与关系，校验学生存在且绑定码与当前 `Student.bind_code` 匹配后建立亲子绑定。同一家长已存在 active 绑定返回业务冲突；已存在 inactive 历史绑定则复用该行（更新关系与绑定码、状态恢复 active），不新建。绑定成功后绑定码即失效。

#### Scenario: 绑定成功
- **WHEN** 家长提交学生 ID 与匹配的绑定码
- **THEN** 建立一条 active 绑定并返回 binding_id，该绑定码不再可用于发起新绑定

#### Scenario: 绑定码不匹配
- **WHEN** 家长提交的绑定码与 Student.bind_code 不匹配
- **THEN** 返回业务规则冲突错误，不创建任何绑定记录

#### Scenario: 已存在 active 绑定
- **WHEN** 该家长与该学生已存在 active 绑定
- **THEN** 返回业务冲突错误，不重复创建

#### Scenario: 复用 inactive 历史绑定
- **WHEN** 该家长与该学生存在 inactive 历史绑定且提交新的有效绑定码
- **THEN** 复用该行更新关系与绑定码并恢复 active，而非新建行

### Requirement: 家长解绑学生
系统 SHALL 提供 `DELETE /api/parent/bind/{binding_id}`，仅允许家长解绑本人名下的绑定；解绑后绑定状态置 inactive（软删，保留记录），该家长立即失去对该学生的数据访问。

#### Scenario: 解绑本人绑定
- **WHEN** 家长解绑属于自己的绑定
- **THEN** 绑定状态置 inactive，该家长对该学生数据访问返回 403"未绑定该学生"

#### Scenario: 越权解绑他人绑定
- **WHEN** 家长尝试解绑不属于自己的绑定
- **THEN** 返回 404（绑定不存在）或 403，绑定记录不变

### Requirement: 已绑定子女列表
系统 SHALL 提供 `GET /api/parent/children`，返回当前家长全部 active 绑定子女的概要信息（学生 ID、姓名、班级名），响应不含任何其他学生信息。

#### Scenario: 返回已绑定子女
- **WHEN** 家长请求子女列表
- **THEN** 返回其全部 active 绑定子女概要

#### Scenario: 无绑定子女
- **WHEN** 家长无任何 active 绑定
- **THEN** 返回空列表

### Requirement: 子女报告查询
系统 SHALL 提供 `GET /api/parent/child/{student_id}/report`，返回该家长 active 绑定子女的家长视角报告：本周练习数量、正确率、连续学习天数、累计练习总量、知识掌握概览与通俗学习特点。响应 SHALL 不包含子女原始答题详情、排名、教师详细评语与绑定码。

#### Scenario: 查看绑定子女报告
- **WHEN** 家长请求其 active 绑定子女的报告
- **THEN** 返回家长视角报告，且不含原始答题详情、排名、教师评语与绑定码

#### Scenario: 请求未绑定学生报告
- **WHEN** 家长请求某非绑定学生的报告
- **THEN** 返回 403"未绑定该学生"

#### Scenario: 学生不存在
- **WHEN** 请求的学生 ID 不存在
- **THEN** 返回 404，不泄露任何学生信息

### Requirement: 周报查询与生成
系统 SHALL 提供周报查询与生成：`GET /api/parent/child/{student_id}/weekly` 返回当周周报（若当周未生成则懒生成）；`POST /api/parent/child/{student_id}/weekly/generate` 手动触发生成。周报为 JSON（`summary`/`detail`/`advice`/`no_data`），同一周（周一到周日）每学生仅生成一份，重复触发返回已缓存内容。周报内容 SHALL 遵守文档 33 §7 通俗化约束：不提及排名、不与其他学生比较、不使用负面词、化学术语按转换表通俗化、长度受限。

#### Scenario: 懒生成周报
- **WHEN** 家长请求某绑定子女周报且当周尚未生成
- **THEN** 生成并返回当周周报 JSON

#### Scenario: 周内去重
- **WHEN** 同一周内再次请求或触发生成
- **THEN** 返回已缓存的周报，不重复执行 LLM 生成

#### Scenario: 无练习数据
- **WHEN** 当周无练习记录
- **THEN** 返回 `no_data=true` 的周报，仅 `summary` 说明"本周暂无练习记录"

#### Scenario: 手动生成周报
- **WHEN** 家长手动触发周报生成
- **THEN** 生成当周周报；若当周已存在则返回缓存内容

#### Scenario: 周报生成失败
- **WHEN** LLM 调用失败且纠错重试耗尽
- **THEN** 返回可读错误提示，周报保持未生成状态以便重试

### Requirement: AI 通俗解读
系统 SHALL 提供 `POST /api/parent/child/{student_id}/report/ai-summary`，对绑定子女报告调用 LLM 生成通俗解读，遵循文档 33 §7 通俗化约束（术语转换、不制造焦虑、可操作建议）。

#### Scenario: 获取通俗解读
- **WHEN** 家长请求绑定子女的 AI 解读
- **THEN** 返回通俗化解读文本

#### Scenario: 解读生成失败
- **WHEN** LLM 调用失败且重试耗尽
- **THEN** 返回可读错误提示，不返回半成品内容

### Requirement: 通知列表与已读
系统 SHALL 提供 `GET /api/parent/notifications`（分页，按创建时间倒序，返回通知类型、标题、内容、创建时间与已读状态）与 `PUT /api/parent/notifications/{notification_id}/read`（标记已读）。通知归属的家长身份 SHALL 从令牌解析，不接受客户端通过参数指定他人 parent_id。

#### Scenario: 分页通知列表
- **WHEN** 家长携带令牌请求通知列表
- **THEN** 返回该家长自身的通知（limit/offset 分页，时间倒序，含已读状态）

#### Scenario: 标记已读
- **WHEN** 家长标记属于自己的一条通知为已读
- **THEN** 该通知 is_read 置 true

#### Scenario: 越权读取他人通知
- **WHEN** 请求参数携带与令牌家长不一致的 parent_id
- **THEN** 忽略参数，仅返回令牌对应家长的通知

#### Scenario: 标记他人通知已读
- **WHEN** 家长尝试标记不属于自己的通知为已读
- **THEN** 返回 404 或 403，该通知状态不变
