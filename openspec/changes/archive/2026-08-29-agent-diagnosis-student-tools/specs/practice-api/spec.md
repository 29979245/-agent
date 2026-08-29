## ADDED Requirements

### Requirement: 自适应练习确认落库
系统 SHALL 提供 `POST /api/practice/adaptive/confirm` 端点：接收教师确认后的自适应练习批量预览（每生含知识点、难度档位与选中题目引用），逐生创建练习记录（`exam_type=practice`）并复制选中题目副本入库，返回每生的 `practice_id` 与确认结果。端点 SHALL 仅教师角色可调用且需审批门控，未经审批确认 SHALL 不创建任何练习记录；Agent 预览工具自身 SHALL 不写入练习记录。

#### Scenario: 确认后逐生落库
- **WHEN** 教师确认自适应练习预览并提交批量
- **THEN** 为每名学生创建练习记录并复制选中题目副本，返回逐生 practice_id 与确认结果

#### Scenario: 未审批不落库
- **WHEN** 请求未经教师审批确认
- **THEN** 返回审批阻塞错误，不创建任何练习记录
