# ChemAI 领域术语表

> 从产品设计文档（Part 4）提炼的核心领域术语。英文名以设计文档中的枚举值/字段名为准。
> 每项格式：**中文名（英文名）**：一句话定义。

---

## 一、核心实体（Core Entities）

- **学校（School）**：顶层组织容器，包含年级与教师，是数据隔离的最高边界。
- **年级（Grade）**：学校下的学年层级，如"高一"；含所属学年字段。
- **班级（Class）**：年级下的教学单元，含学段（高中/初中）与学科（化学），是数据隔离的核心边界。
- **统一账户（Account）**：教师/学生/家长共用的登录实体，通过角色字段区分身份并一对一关联角色记录。
- **教师（Teacher）**：归属于学校，含账号状态（待审核/已通过/已拒绝）与角色（系统管理员/教务管理员/学科组长/普通教师）。
- **任课关系（TeacherClassSubject）**：教师与班级的多对多任教关系，标注是否班主任。
- **学生（Student）**：系统核心实体，存储障碍画像、练习追踪、6 位家长绑定码。
- **家长（Parent）**：通过绑定码与学生建立亲子关系的监护人。
- **亲子绑定（StudentParentBinding）**：家长与学生的绑定关系记录，含绑定状态与关系（父亲/母亲/其他监护人）。
- **考试记录（ExamRecord）**：一次考试/练习/作业记录，归属于班级，含考试类型与错题统计。
- **题目（Question）**：一份完整化学试题，含正文、选项、答案、解析、知识点标签、难度、来源、四维审核状态。
- **学生作答（StudentAnswer）**：学生针对某题目的作答记录，含是否正确、障碍类型标签、连续错误/正确计数。
- **学生提交（StudentSubmission）**：一次考试中某学生答题卡的独立提交，含原始/批改后图片、答案列表、总分。
- **真题集（HistoricalExam）**：历年高考真题/模拟题的归类组织（如"全国卷""湖南卷"），是 RAG 知识底座。
- **真题集条目（QuestionSetItem）**：真题集与题目的多对多关联，含排序字段。
- **题库文件夹（QuestionSet）**：教师自建的两级题库顶层组织单元，含排序；系统预设文件夹不可删除。
- **知识点（KnowledgePoint）**：化学知识点记录，含分类、关联 PubChem 编号、动态错误率；知识图谱节点。
- **家长通知（ParentNotification）**：推送给家长的消息，含类型（学习报告/预警提醒/教师消息）与已读状态。
- **预警日志（WarningLog）**：学情预警记录，追踪是否已通知教师/家长/学生本人。
- **复习任务（ReviewTask）**：错题自动创建的间隔复习任务，按艾宾浩斯 6 级安排。
- **障碍配置（BarrierConfig）**：教师自定义的班级诊断阈值配置（各障碍阈值 + 掌握标准 + 自动同步开关）。

---

## 二、学习概念（Learning Concepts）

- **最近发展区（ZPD, Zone of Proximal Development）**：学生"跳一跳够得着"的难度区间，自适应练习的个性化目标理论。
- **艾宾浩斯遗忘曲线（Ebbinghaus Forgetting Curve）**：间隔复习的理论依据，决定复习间隔安排。
- **间隔复习等级（Review Level）**：1→6 级复习状态机（1 天/3 天/7 天/14 天/30 天/不再安排），答对升级、答错降级。
- **掌握标准（Mastery Threshold）**：同一知识点连续答对 N 次（默认 3）判定为已掌握。
- **自适应练习（Adaptive Practice）**：依据学生障碍画像与薄弱知识点生成个性化 ZPD 练习。
- **薄弱知识点（Weak Knowledge Points）**：学生诊断结果中标注的知识短板列表。
- **学习计划（Learning Plan）**：教师为学生制定的个性化计划，含每日任务列表；状态机：预览→已应用/已发送家长/已删除。
- **错题变式训练（Variant Training）**：对错题生成同知识点变式题进行强化训练。
- **连续错误/连续正确计数（consecutive_errors / consecutive_correct）**：作答表上的计数器，驱动障碍诊断与掌握判定。

---

## 三、诊断概念（Diagnosis Concepts）

- **三维障碍模型（Barrier Type）**：错题根因的三分法枚举 `concept / reading / expression`。
- **概念理解型（concept）**：不理解底层化学概念与原理，停留在记忆层面；典型表现是混淆相似概念、无法解释推理。
- **审题障碍型（reading）**：读题不完整或落入陷阱，信息提取不足；典型表现是概念掌握但答非所问、遗漏关键词。
- **表述障碍型（expression）**：理解正确但表达不规范，化学用语书写不合规；典型是方程式书写错、漏写单位/有效数字。
- **规则引擎初筛（Rule Engine Pre-screening）**：基于关键词规则库的低成本预分类，置信度控制在 0.5-0.7，未来作为 LLM 的校验与降级兜底。
- **LLM 深度分析（LLM Deep Analysis）**：以教育心理学专家角色，综合历史错题/题目/作答/答案四输入给出 `barrier_type + confidence + reasoning + suggestion`。
- **综合判定（Aggregation）**：按置信度三级采纳——≥0.8 自动采纳、0.7-0.8 采纳但标注需关注、<0.7 建议人工复核。
- **置信度（Confidence）**：LLM 诊断对判定结论的自信心值，范围 0.0-1.0。
- **学生障碍画像（Barrier Profile）**：学生障碍类型分布 JSON `{"concept":0.30,"reading":0.50,"expression":0.20}`（和为 1），由诊断引擎异步聚合更新。
- **主导障碍类型（dominant_barrier）**：障碍画像中占比最高的类型，用于学生卡片与班级分布统计。
- **教师覆盖（Override）**：教师手动推翻 AI 诊断的机制，覆盖时按 90%/5%/5% 写入新画像并记录操作日志。
- **干预建议（Intervention Suggestion）**：按障碍类型给出的差异化学习建议（概念→思维导图、审题→划线法、表述→规范化训练）。
- **严重度标记（Severity Mark）**：障碍占比 ≥60% 红色 / 40-60% 黄色 / <40% 绿色。
- **诊断配置阈值（Barrier Config Thresholds）**：concept_threshold=3 / reading_threshold=2 / expression_threshold=3 / mastery_threshold=3 / auto_sync_to_student=false。

---

## 四、题目与考试（Question & Exam）

- **题型（Question Type）**：`choice` 选择题 / `fill` 填空题 / `calc` 计算题 / `experiment` 实验题 / `inference` 推断题。
- **选择题（Choice Question）**：4 个选项 + 1-2 个陷阱选项，标注正确选项的题型。
- **填空题（Fill-in-the-Blank Question）**：下划线标记填空位、每空 1-3 个的题型。
- **计算题（Calculation Question）**：含具体数值条件，答案须含分步计算的题型。
- **实验题（Experiment Question）**：描述化学实验情境、含 2-3 个子问题的题型。
- **推断题（Inference Question）**：给出物质转化线索、要求推断未知物质的题型。
- **难度（Difficulty）**：四级枚举 `easy 简单 / medium 中等 / hard 困难 / competition 竞赛`；竞赛级仅手动录入、AI 不出。
- **题目来源（Question Source）**：AI 生成 / 手动录入 / 日常练习 / OCR 导入。
- **出题模式（Generation Mode）**：AI 生成（配置参数+LLM+RAG）、手动录入（表单）、OCR 扫描导入（图片→结构化题目）。
- **蓝本题（Blueprint Question）**：变体生成所基于的源真题，其知识点/难度/正文注入 LLM prompt。
- **变体（Variant）**：五类变体维度——数值变体、物质替换、选项重组、题干重写、难度调整。
- **四维安全审核（Four-Dimensional Audit）**：系数配平 / 反应条件 / 产物正确性 / 分子结构四维度审核，配平要求 100% 准确率。
- **题目四维审核（Four-Dimension Review）**：AI 生成题目的质量审核，区别于四维安全审核（后者审核方程式正确性，前者审核整道题质量，是两套独立系统）。四维度——科学性 / 难度匹配 / 知识点覆盖 / 区分度；每维 0-100 分，综合为加权和（科学性×0.4 + 难度×0.25 + 知识×0.2 + 区分×0.15）。
- **审核状态（Audit Status）**：`passed 通过 / warning 警告 / blocked 阻断`；阻断题不可下发给学生。题目级综合分映射：≥80 通过 / 70-79 警告 / <70 阻断；科学性单维 <70 直接阻断。
- **审核状态机（Audit State Machine）**：passed → 教师 approve 入库；warning → 教师 approve 放行（带复核标记）或打回重生成；blocked → 自动重生成 ≤3 次（每次重新两层审核），3 次仍 blocked → 标记"出题失败"（不自动替换，教师决定是否手动替换）。
- **审核触发范围（Audit Trigger Scope）**：AI 生成走两层审核；手动录入/OCR 导入只过方程式级硬闸（题目级跳过）；balance_equation 工具只跑方程式级；学生对话/实验模拟本次不纳入。
- **考试状态机（Exam State）**：`Draft 草稿 → AddingQuestions 添加题目 → Published 已发布 → InProgress 进行中 → Completed 完成`（API 层另有 Finalized 终结统计）。
- **向量检索（Vector Retrieval）**：ChromaDB 检索真题，两层策略——关键词 Top-20 缩小候选 + 向量精筛 Top-K。
- **知识图谱（Knowledge Graph）**：以 category 分类的扁平结构存储约 20+ 核心知识点（含 related_kps 关联边），支撑出题与诊断。
- **三层搜索（Three-Layer Search）**：本地关键词 → 向量召回 → 联网补齐的真题搜索策略。
- **试卷导出（Paper Export）**：导出 Word/PDF 试卷，`with_answers` 区分学生版/教师版（教师版标红答案、绿标解析）。

---

## 五、Agent 概念（Agent Concepts）

- **v2 单 Agent（Single-Agent v2）**：LangGraph `create_react_agent`，全量工具注入单个 ReAct Agent 由 LLM 自选，路由准确率 87%、延迟更低。
- **v1 多 Agent（Multi-Agent v1）**：Coordinator 调度 + Router 分发 + 6 个专家子智能体，保留为回退版本。
- **意图分类器（Gateway）**：请求进入 Agent 前的预分类，区分 chat（对话）与 navigate（导航）两类意图，LLM 语义分类优先 + 关键词兜底。
- **规划器（PlanGenerator / Planner）**：将复杂教学目标拆解为最多 6 步的 PlanStep 结构化执行步骤，支持依赖注入与单步兜底。
- **上下文管理器（Context Manager）**：三层保留策略裁剪过长对话历史（摘要→关键词命中→最近 6 条→当前输入），解决 token 爆炸。
- **护栏（GuardState）**：请求级安全基础设施，四层检查——前置条件、调用限次、去重、审批门控。
- **审批流程（Approval）**：破坏性操作（布置练习、删除题库）先经审批：Agent 暂停 → 前端确认卡片 → 教师确认 → 恢复执行。
- **Persona（Persona）**：决定 Agent 系统提示、可用工具集与数据权限的配置 YAML；共 4 个——Teacher 教研助手 / Student 化学助教（学生端）/ Tutor 化学助教（通用）/ Parent 家长助手。
- **工具元数据注册表（TOOL_META）**：以工具函数为键注册可用 persona 列表与每轮最大调用次数 call_limit。
- **检查点（Checkpoint）**：LangGraph 对话状态持久化到 SQLite（AsyncSqliteSaver），支撑多轮保持、审批中断恢复、对话隔离。
- **长期记忆（Long-term Memory / AsyncSqliteStore）**：跨会话键值存储学生诊断历史与教师偏好，best-effort 写入。
- **工作记忆（Working Memory）**：固定容量滑动窗口（最近约 20 条消息）的即时上下文。
- **情景记忆（Episodic Memory）**：本次对话内提取的结构化关键事件，随请求销毁。
- **SSE 适配器（SSE Adapter）**：将 Agent 执行过程转换为 11 种结构化事件流（phase/tool_call/tool_result/text/component/navigate/populate/action/exam_images/error/done）。
- **阶段状态（Phase States）**：thinking 分析中 / executing 执行中 / planning 生成计划 / reply 回复中 / awaiting_approval 等待审批。
- **模型工厂（Model Factory）**：统一获取 LLM 实例的接口，屏蔽多 Provider API 差异，Agent 引擎与工具系统各用一套。
- **依赖注入容器（DI Container）**：Agent 运行上下文，含 student_id / student_profile / persona / episodic / provider_name，所有工具经其访问。
- **审计日志（Audit）**：JSONL 格式记录所有技能执行 + 内存环形缓冲（100 条）。
- **MCP 服务器（MCP Server）**：外部系统经 Model Context Protocol 调用工具的接口，注册 16 个 MCP 工具，端点 `/api/mcp`。

### 工具组（Agent Tools）
- **出题与题库工具组**：search_exam_bank 搜索真题 / web_search 联网搜索 / show_exam_workbench 出题面板 / generate_questions 生成题目 / save_to_bank 保存入库 / list_banks 题库列表 / delete_bank 删除题库（需审批）。
- **诊断与学生工具组**：diagnose_barrier 诊断障碍 / show_diagnosis 诊断面板 / show_students 学生列表 / weekly_report 生成周报 / assign_adaptive_practice 布置自适应练习（需审批）/ generate_learning_plan 生成学习计划 / send_learning_plan 发送计划。
- **辅导工具组**：ionic_equation_tutor 离子方程式 / stoichiometry_tutor 化学计量 / redox_tutor 氧化还原 / equilibrium_tutor 化学平衡 / periodic_law_tutor 周期律 / organic_tutor 有机推断 / chemistry_tutor 通用辅导 / simulate_experiment 模拟实验 / balance_equation 配平方程式。
- **OCR 与批改工具组**：query_ocr_progress 查询进度 / grade_answer_sheets 批改答题卡 / save_grading_results 保存批改结果。
- **记忆工具组**：memory_student_get 读取学生记忆 / memory_teacher_get 读取教师记忆。
- **家长报告工具组**：generate_parent_report 生成家长报告 / send_report_to_parent 发送报告给家长。
- **浏览器工具组（5 个）**：browse_navigate / browse_read / browse_click / browse_input / browse_screenshot，Playwright 无头浏览器。

---

## 六、OCR 概念（OCR Concepts）

- **上传会话（UploadSession）**：教师上传答题卡后追踪整个处理流程的会话实体。
- **上传会话状态机（UploadSession State Machine）**：`UPLOADED 已上传 → PREVIEWING 预览中 → READY 就绪 → (IMPORTING 导入中 → IMPORTED 已导入 | GRADING 批改中 → GRADED 已批改) → DONE 完成`；另有 DISCARDED 取消 / ERROR 出错终态。
- **OCR 任务（OCRTask）**：单张答题卡的识别与批改任务；状态机 `pending 待处理 → processing 处理中 → done 完成 / failed 失败`。
- **批次（Batch）**：批量上传创建的批次标识，前端按批次查询任务进度。
- **三引擎（Three Engines）**：百度 OCR（手写识别主力）/ MinerU（PDF 印刷解析）/ VLM 多模态（兜底，GLM-4V / MiMo）。
- **降级链（Degradation Chain）**：PDF→MinerU 优先→百度→VLM；图片→百度优先→MinerU→VLM；混合→MinerU+百度并行→合并→VLM。
- **LLM 批改（LLM Grading）**：基于 OCR 结果对主观题/化学方程式做语义判定；与百度 correct_edu 客观题判定并行。
- **答案来源选择（Answer Source Selection）**：批改参考答案优先级——题库匹配 > 教师录入 > LLM 自判。
- **correctResult 编码（correctResult）**：百度批改返回编码——0 未处理 / 1 正确 / 2 错误 / 3 未作答。
- **学生匹配（Student Match）**：OCR 从答题卡提取学号与姓名（正则 8-11 位学号），未知学号置 `unknown`、姓名置"待识别"。
- **批改痕迹（Grading Mark）**：correct_edu 在答题卡上标注逐题对错（正确/错误/未作答）并附批注。
- **降级标记（fallback_used / degraded）**：Vision 兜底成功置 `fallback_used=true`；correct_edu 不可用退化为 doc_analysis + LLM 字符串比较并标记 `degraded=True`。

---

## 七、数据与质量（Data & Quality）

- **三库分离（Three Databases）**：主库（业务数据）/ Agent 检查点库（对话状态）/ Agent 长期记忆库（跨会话记忆）。
- **RAG（Retrieval-Augmented Generation）**：以历年真题为上下文注入 LLM prompt 生成题目/变体。
- **Golden 数据集（Golden Dataset）**：100 条标注的评测样本，驱动 L3 质量评测与回归对比。
- **Evals 基线（Baseline）**：`baseline.json` 记录各评测项基线，`run_evals --compare baseline.json` 做劣化检测。
- **L1 单元评测（L1 Unit）**：纯函数逻辑评测，通过标准 ≥95%，pre-commit 触发。
- **L2 集成评测（L2 Integration）**：API 端点行为评测，通过标准 ≥90%，每个工具组完成时触发。
- **L3 质量评测（L3 Quality）**：AI 内容质量评测（科学性/诊断准确率/辅导安全），通过标准 ≥70%。
- **指标口径（Core Metrics）**：路由准确率 ≥85%、诊断覆盖率 >90%、OCR 准确率 >95%、选择题批改准确率 >99%。
- **审核验收指标（Audit Acceptance）**：方程式级 86/86 确定性测试 100%（配平红线）、条件/产物召回率 ≥80%；题目级科学性准确率 ≥75%；两层合成 overall_status 判定正确率 ≥90%。
