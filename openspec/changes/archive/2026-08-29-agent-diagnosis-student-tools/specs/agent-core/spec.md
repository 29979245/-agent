## MODIFIED Requirements

### Requirement: 工具切片——诊断组
系统 SHALL 提供诊断组 7 个工具：`diagnose_barrier`（个体或班级两级诊断，返回三维障碍分布与主导类型）、`show_diagnosis`（内联渲染诊断图表面板）、`show_students`（三模式：无班级列出班级/有班级学生卡片/有过滤按障碍筛选）、`weekly_report`（LLM 生成 200 字自然语言周报，通俗不制造焦虑）、`assign_adaptive_practice`（为班级生成个性化 ZPD 练习预览，不落库；教师确认后由前端调用 API 持久化）、`generate_learning_plan`（跳转学生管理页触发学习方案生成）、`send_learning_plan`（持久化学习计划并通知学生）；诊断工具 SHALL 支持智能名称解析（纯数字 ID 或中文姓名模糊匹配）与班级名灵活匹配（中文数字→阿拉伯数字）。家长角色 SHALL 仅可访问已绑定子女的学情（doc 30 §4.2）：携带班级参数调用班级级诊断 SHALL 返回 ForbiddenError，班级统计分布仅 teacher 角色可获取。

#### Scenario: 个体诊断
- **WHEN** 提供 student_id 或姓名调用 diagnose_barrier
- **THEN** 返回三维障碍分布与主导类型

#### Scenario: 智能名称解析多结果
- **WHEN** 中文姓名模糊匹配命中多个学生
- **THEN** 返回候选列表供选择

#### Scenario: 自适应练习预览不落库
- **WHEN** Agent 调用 assign_adaptive_practice
- **THEN** 返回每生预览（ZPD 难度 / 难度档位 / 主导障碍 / 知识点 / 选中题目引用），不创建练习记录、不复制题目入库；教师确认后由前端调用 REST API 持久化

#### Scenario: 布置练习需审批
- **WHEN** 教师在前端确认自适应练习预览并提交批量
- **THEN** 由确认落库 API 审批后逐生创建练习记录；未经教师确认不创建任何练习记录（审批门控从工具移至 API 确认端点，doc 28 §六）

#### Scenario: 家长班级诊断受限
- **WHEN** parent 角色携带 class_id/class_name 调用 diagnose_barrier
- **THEN** 返回 ForbiddenError（家长仅支持个体诊断），不返回任何班级学生画像
