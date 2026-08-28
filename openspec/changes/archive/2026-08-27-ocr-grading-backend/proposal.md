## Why

OCR 批改是教学工具链的入口环节：教师上传答题卡后，系统自动完成 OCR 识别、LLM 批改、结果入库并触发障碍诊断。当前只落下了数据模型骨架（`UploadSession`/`OCRTask`/`StudentSubmission` 及枚举），引擎、状态机逻辑、批改、调度与 API 全部为空（`app/services/ocr/` 为空、`app/api/v1/` 无 ocr 路由），端到端链路无法跑通。

## What Changes

- 实现 `UploadSession` 状态机：10 态流转（uploaded→previewing→ready→importing/imported 或 grading/graded→done）+ 终态守卫（终态做状态变更抛异常）+ 乐观锁版本递增。
- 实现百度 Auth Token 管理：内存缓存 + 300s 安全边距（距到期 ≤300s 刷新，否则复用缓存）+ 凭据缺失报错。
- 实现双引擎路由与降级链：图片→百度 doc_analysis→VLM 兜底；PDF→MinerU→百度→VLM。MinerU 做运行时可用性检测（try-except 导入 + `check_available()`，模型未下载/磁盘不足则标记不可用并降级百度+Vision，测试全 mock）。
- 实现 LLM 批改引擎：答案来源三模式（题库匹配/教师录入/LLM 自判）+ 答案标准化比较（去空白+大写+相等）+ 自判安全模式（无法确定答案标记需人工复核）。
- 实现 OCRTask 调度：APScheduler 5s 轮询（pending→processing→done/failed）+ 重试（failed→pending，清空错误与识别结果）。
- 新增批改判卷 API：上传批次、查询批次状态、重试任务、教师任务列表、触发批改、保存结果、查询批改结果、服务可用性检查。
- 保存结果分档落库：模式1（有考试/题库匹配）展开 `StudentAnswer` 行并触发诊断；模式2/3（无考试）只落 `StudentSubmission.answer_list`，不展开（ADR 0003）。
- 依赖零新增：百度走 `httpx`，VLM 复用现有 LLM fallback（MiMo→qwen→deepseek / langchain-openai）。

## Capabilities

### New Capabilities
- `ocr-grading`: OCR 批改管线后端——上传会话状态机、双引擎路由、LLM 批改引擎、OCRTask 调度与批改判卷 API。批改结果按答案来源分档落库并接线障碍诊断。

### Modified Capabilities
- （无）——`data-models` 主 spec 已含 OCR 三实体需求（模型已实现），本 change 不改变其定义。

## Impact

- `app/services/ocr/`：新增 `state_machine.py`（UploadSession 状态机）、`engines/`（`baidu_engine`/`mineru_engine`/`vlm_fallback` + `OCRProvider` 协议 + 路由）、`token.py`（百度 Token 管理）、`grading.py`（LLM 批改）、`scheduler.py`（OCRTask 轮询）。
- `app/api/v1/ocr.py`：新增 `/api/ocr/*` 批改判卷路由，`app/main.py` 注册。
- `app/db/models/ocr.py`：已存在，可能补充字段（如重试语义所需）。
- `app/config.py`：OCR 配置已存在，按需补充。
- `app/services/diagnosis`：**只读消费**——保存结果后调用既有 `run-llm` 批量诊断接口触发。
- 依赖：无新增（httpx / langchain-openai / apscheduler 均已存在）。
- 测试：`tests/unit/test_ocr_state_machine.py`、`tests/unit/test_baidu_token.py`、`tests/unit/test_ocr_engines.py`、`tests/unit/test_llm_grading.py`、`tests/unit/test_ocr_scheduler.py`、`tests/integration/test_ocr_api.py`。

> **测试数据标注（51 号文档 L69）**：百度 OCR 密钥未配置（`.env` 不存在，`.env.example` 中 `BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY` 为空）——**所有 OCR 测试使用 mock 数据**（mock httpx、mock LLM、mock MinerU 不可用）；真实凭据配置后由集成测试覆盖。
