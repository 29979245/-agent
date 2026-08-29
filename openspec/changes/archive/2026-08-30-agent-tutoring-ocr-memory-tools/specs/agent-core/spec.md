## ADDED Requirements

### Requirement: 工具切片——辅导组
系统 SHALL 提供辅导组 9 个工具：6 个苏格拉底式专题辅导（`ionic_equation_tutor`、`stoichiometry_tutor`、`redox_tutor`、`equilibrium_tutor`、`periodic_law_tutor`、`organic_tutor`）、`chemistry_tutor`、`simulate_experiment`、`balance_equation`；各工具 SHALL 遵循 doc 30 §3.4 的输入/限次/角色约束（专题辅导限次 5 仅学生、chemistry_tutor 限次 3 全体、simulate_experiment 限次 2 学生与辅导、balance_equation 限次 3 辅导与教师），限次定义于 TOOL_META。

6 个专题辅导工具 SHALL 由同一工厂函数生成（doc 30 §8.2），按各自方法分步引导而非直接给答案：离子方程式（判断可拆物质→写成离子→删不变离子→检查守恒）、化学计量（提取已知量→选公式→列关系式→分步计算）、氧化还原（标化合价→找升降→电子守恒配平）、化学平衡（分析平衡体系→勒夏特列原理→三段式计算）、周期律（位置→结构→性质推断）、有机推断（逆合成分析+官能团转化）；输入 SHALL 含方程式、题目与学生输入。

`chemistry_tutor` SHALL 接收问题、学生水平与角色：teacher 角色 SHALL 输出 800 字教研分析（考点分布+教学策略+学生误区），student/tutor 角色 SHALL 输出 500 字引导教学；中文关键词（什么是/怎么做/原理）命中 SHALL 路由至本工具。

`simulate_experiment` SHALL 接收实验名称，由 LLM 生成实验报告：目的、仪器、步骤、现象、方程式、原理、安全提醒、考点。

`balance_equation` SHALL 接收方程式，用确定性算法完成配平并执行四维审核，返回两侧各元素原子计数与配平结果，不依赖 LLM 配平。

#### Scenario: 专题苏格拉底引导
- **WHEN** 学生请求某专题辅导（如离子方程式）
- **THEN** 按该专题方法分步引导，不直接给出答案

#### Scenario: 通用辅导角色分流
- **WHEN** teacher 角色调用 chemistry_tutor
- **THEN** 输出 800 字教研分析；student 角色调用则输出 500 字引导教学

#### Scenario: 配平确定性零误差
- **WHEN** 调用 balance_equation 配平方程式
- **THEN** 返回两侧各元素原子计数相等且结果确定，不依赖 LLM 配平

#### Scenario: 模拟实验报告
- **WHEN** 调用 simulate_experiment
- **THEN** 返回含目的、仪器、步骤、现象、方程式、原理、安全提醒、考点的实验报告

### Requirement: 工具切片——OCR批改组
系统 SHALL 提供 OCR 批改组 3 个工具：`query_ocr_progress`、`grade_answer_sheets`、`save_grading_results`；各工具 SHALL 遵循 doc 30 §3.5 的角色约束（仅教师，限次 3/2/2 定义于 TOOL_META）；写工具 `grade_answer_sheets` 与 `save_grading_results` SHALL 纳入 Guard 第 4 层审批门控，未经确认不得执行。

`query_ocr_progress` SHALL 接收教师 ID 与批次 ID，按批次聚合 OCR 任务进度（完成/失败/等待百分比），返回批次摘要与每张状态，为只读工具。

`grade_answer_sheets` SHALL 接收教师 ID、批次 ID 与考试 ID，对已完成 OCR 的任务批量执行 LLM 批改，返回批改汇总与逐题判定；SHALL 只计算不落库，写库由 `save_grading_results` 承担。

`save_grading_results` SHALL 接收教师 ID 与批次 ID，逐学生校验学号已注册后写入作答记录并自动触发障碍诊断，返回保存数量与诊断触发确认。

#### Scenario: 批次进度聚合
- **WHEN** 教师调用 query_ocr_progress 提供教师 ID 与批次 ID
- **THEN** 返回完成/失败/等待百分比与每张状态

#### Scenario: 批量批改不落库
- **WHEN** 调用 grade_answer_sheets 对已完成 OCR 任务批量批改
- **THEN** 返回批改汇总与逐题判定，不写入任何作答记录

#### Scenario: 批改与保存需审批
- **WHEN** Agent 调用 grade_answer_sheets 或 save_grading_results
- **THEN** 进入审批流程，未确认不执行批改或写库

#### Scenario: 保存触发诊断
- **WHEN** 保存批改结果
- **THEN** 逐学生写入作答记录并触发障碍诊断，返回保存数量与诊断触发确认

### Requirement: 工具切片——记忆组
系统 SHALL 提供记忆组 2 个工具：`memory_student_get`（读取学生诊断历史最近 5 条与当前学习计划，全体角色可用）、`memory_teacher_get`（读取教师偏好设置，仅教师）；读取 SHALL 来自长期存储 `LongTermStore`；障碍诊断完成 SHALL 触发学生记忆写入（`push_student_diagnosis`），使读取有数据可依。

`memory_student_get` SHALL 按学生 ID 与记忆类型读取长期存储中的诊断历史（最近 5 条）与当前学习计划，返回诊断历史与学习计划；student 角色 SHALL 仅可读取自身记忆，跨学生访问返回 ForbiddenError。

`memory_teacher_get` SHALL 读取教师偏好设置（教学风格、难度偏好、班级配置），返回教师配置数据；仅 teacher 角色可调用。

记忆写入 SHALL 在障碍诊断完成时触发：诊断结果（含 LLM 不可用时降级规则诊断）SHALL 写入该学生长期存储。

#### Scenario: 学生读自身记忆
- **WHEN** student 角色读取自身诊断历史与学习计划
- **THEN** 返回最近 5 条诊断历史与当前学习计划

#### Scenario: 跨学生读取受限
- **WHEN** student 角色读取其他学生记忆
- **THEN** 返回 ForbiddenError

#### Scenario: 教师读偏好
- **WHEN** teacher 角色调用 memory_teacher_get
- **THEN** 返回教学风格、难度偏好、班级配置数据

#### Scenario: 诊断写记忆
- **WHEN** 障碍诊断完成（含 LLM 不可用降级为规则诊断）
- **THEN** 诊断结果写入学生长期存储，后续可被 memory_student_get 读取

### Requirement: 工具切片——家长报告组
系统 SHALL 提供家长报告组 2 个工具：`generate_parent_report`（生成报告预览，不发送）与 `send_report_to_parent`（推送周报到已绑定家长，发送需审批）；两者 SHALL 仅 teacher 角色可调用（doc 30 §3.7）；parent 角色 SHALL 不得生成或发送家长报告。

`generate_parent_report` SHALL 接收学生姓名与 ID，聚合练习、诊断、知识点数据，生成家长可读报告预览文本与需确认标记，不发送给家长。

`send_report_to_parent` SHALL 接收学生 ID 与报告数据，推送周报到已绑定家长的通知列表，返回发送确认；SHALL 纳入 Guard 第 4 层审批门控，未经确认不推送。

#### Scenario: 生成报告预览
- **WHEN** teacher 调用 generate_parent_report
- **THEN** 返回家长可读报告预览与需确认标记，不发送

#### Scenario: 发送需审批
- **WHEN** Agent 调用 send_report_to_parent
- **THEN** 进入审批流程，未确认不推送

#### Scenario: 发送确认
- **WHEN** 教师确认发送
- **THEN** 推送周报到已绑定家长的通知列表，返回发送确认

#### Scenario: 家长角色受限
- **WHEN** parent 角色调用 generate_parent_report 或 send_report_to_parent
- **THEN** 返回 ForbiddenError，不生成或发送任何报告
