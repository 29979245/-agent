## Purpose

OCR 批改管线后端把教师上传的答题卡自动完成 OCR 识别、LLM 批改、结果入库并触发障碍诊断，是教学工具链的入口环节。

## ADDED Requirements

### Requirement: 上传会话状态机
系统 SHALL 提供上传会话状态机，支持 10 态流转：uploaded（已上传）→ previewing（预览中）→ ready（就绪）→ importing/imported（导入中/已导入）或 grading/graded（批改中/已批改）→ done（完成），另有 discarded（取消）/ error（出错）终态。对终态做状态变更 SHALL 抛异常；每次状态变更 SHALL 递增乐观锁版本。

#### Scenario: 正常批改流转
- **WHEN** 上传会话从 uploaded 依次经历 previewing、ready、grading、graded
- **THEN** 会话最终可迁移到 done，且每次迁移后版本号递增

#### Scenario: 终态守卫
- **WHEN** 对已处于 done / discarded / error 任一终态的会话执行状态变更
- **THEN** 系统抛异常并拒绝该变更

### Requirement: 百度认证令牌生命周期
系统 SHALL 管理百度 OAuth 访问令牌：内存缓存复用，距到期超过安全边距（300 秒）时直接复用缓存，距到期不足时触发 client_credentials 刷新；凭据缺失 SHALL 报错并返回配置缺失提示。

#### Scenario: 缓存复用
- **WHEN** 缓存的令牌仍有效且距到期超过 300 秒
- **THEN** 系统直接复用缓存令牌，不发起刷新请求

#### Scenario: 安全边际刷新
- **WHEN** 缓存的令牌距到期不足 300 秒或已过期
- **THEN** 系统触发 client_credentials 刷新并更新缓存

#### Scenario: 凭据缺失报错
- **WHEN** 百度 API 密钥或 Secret 未配置而发起调用
- **THEN** 系统返回错误，提示配置缺失

### Requirement: 引擎路由与降级链
系统 SHALL 按文件类型路由到对应 OCR 引擎（图片→百度 OCR，PDF→MinerU），每层引擎失败 SHALL 才进入下一层降级（图片：百度→VLM→部分结果；PDF：MinerU→百度→VLM），成功即返回不回退。MinerU 引擎不可用（模型未下载或磁盘不足）时 SHALL 标记不可用并降级到百度+Vision。

#### Scenario: 图片路由
- **WHEN** 上传文件为 JPG/PNG 图片
- **THEN** 系统路由到百度 OCR 引擎识别

#### Scenario: PDF 路由与 MinerU 降级
- **WHEN** 上传文件为 PDF 且 MinerU 模型不可用
- **THEN** 系统标记 MinerU 不可用并将该 PDF 转图后走百度 OCR，仍失败再降级 VLM

#### Scenario: 降级链逐层推进
- **WHEN** 百度 OCR 调用失败
- **THEN** 系统进入 VLM 兜底；VLM 也失败时返回部分结果并标记低置信度，不抛未捕获异常中断流程

### Requirement: LLM 批改答案来源选择
系统 SHALL 按优先级确定批改参考答案来源：题库匹配（有考试）→ 教师录入 → LLM 自判。LLM 无法确定正确答案时 SHALL 标记需人工复核，不自动判对错。

#### Scenario: 题库匹配优先
- **WHEN** 批改任务关联了考试且题库中存在该考试题目
- **THEN** 系统按题号从该考试题目提取标准答案进行比对

#### Scenario: 教师录入答案
- **WHEN** 无关联考试但教师逐题录入了答案
- **THEN** 系统以录入答案作为标准进行逐题比对

#### Scenario: 自判安全模式
- **WHEN** 既无关联考试也无教师录入答案
- **THEN** 系统不自动判对错，将该答题卡题目标记为需人工复核

### Requirement: 答案标准化比较
系统 SHALL 对学生答案与标准答案做标准化比较：去除首尾空白并转大写后完全相等才算正确；学生或标准答案为空白、或标准答案标记为自判模式（AUTO）时 SHALL 判为错误。

#### Scenario: 标准化后相等判正确
- **WHEN** 学生答案为 " c " 而标准答案为 "C"
- **THEN** 系统判该题正确

#### Scenario: 空白或自判模式判错
- **WHEN** 学生答案为空、标准答案为空、或标准答案为 AUTO
- **THEN** 系统判该题错误并提示人工复核

### Requirement: OCR 任务调度与重试
系统 SHALL 以固定间隔（5 秒）轮询待处理 OCR 任务，任务状态按 pending → processing → done/failed 流转；失败任务重试时 SHALL 将状态重置为 pending 并清空错误信息与识别结果。

#### Scenario: 轮询拾取与完成
- **WHEN** 调度器拾取 pending 任务并调用 OCR 成功
- **THEN** 任务状态更新为 done，进度置 100%

#### Scenario: 失败记录
- **WHEN** OCR 调用失败或抛异常
- **THEN** 任务状态更新为 failed 并记录错误描述

#### Scenario: 失败重试
- **WHEN** 教师对 failed 任务发起重试
- **THEN** 任务状态重置为 pending，错误信息与识别结果清空

### Requirement: 批改判卷 API
系统 SHALL 提供批改判卷 API：批量上传创建任务并返回批次标识、查询批次聚合状态（总数/完成/失败/待处理）、失败任务重试、查询教师任务列表、触发批改、保存结果、查询批改结果。空文件上传 SHALL 返回 400；无已完成任务触发批改 SHALL 返回 404；系统 SHALL 提供服务可用性检查端点返回各引擎可用状态。

#### Scenario: 批量上传与空文件校验
- **WHEN** 教师以 multipart 上传多张答题卡
- **THEN** 系统保存图片、创建任务并返回批次标识；当上传文件列表为空时返回 400 提示未上传文件

#### Scenario: 批次状态聚合
- **WHEN** 前端查询批次状态
- **THEN** 系统返回该批次总数、完成数、失败数、待处理数及逐任务状态

#### Scenario: 无已完成任务触发批改
- **WHEN** 触发批改但该批次无 status=done 的任务
- **THEN** 系统返回 404 提示未找到已完成的 OCR 任务

### Requirement: 批改结果分档落库
系统 SHALL 按答案来源分档保存批改结果：题库匹配模式（有考试）将逐题结果展开为作答记录并触发障碍诊断；教师录入与 LLM 自判模式（无考试）只保存学生提交的答案列表，不展开作答记录。OCR 提取的学号未在系统中注册的提交 SHALL 静默跳过，不阻塞批次处理。

#### Scenario: 题库匹配展开并触发诊断
- **WHEN** 教师确认保存且该批次关联考试
- **THEN** 系统将逐题判定结果展开为作答记录，并在全部保存成功后触发障碍诊断

#### Scenario: 无考试只落学生提交
- **WHEN** 教师确认保存且无关联考试（教师录入或自判模式）
- **THEN** 系统仅保存学生提交的答案列表，不展开作答记录

#### Scenario: 未注册学生跳过
- **WHEN** 保存时 OCR 提取的学号不在学生表中
- **THEN** 系统跳过该条提交，不写作答记录，其余提交正常保存

### Requirement: 服务可用性检查
系统 SHALL 提供服务状态端点，返回百度 OCR、MinerU、VLM 三个引擎的可用性状态，供前端启动时引导配置。

#### Scenario: 各引擎状态返回
- **WHEN** 前端请求服务可用性检查
- **THEN** 系统返回三个引擎各自的可用状态（是否配置凭据 / 模型是否下载 / 是否配置 Vision 密钥）
