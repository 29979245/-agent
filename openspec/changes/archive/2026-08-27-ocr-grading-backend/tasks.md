## 1. 测试骨架与 OCR 服务包

- [x] 1.1 建 `app/services/ocr/` 子模块（`state_machine.py`/`token.py`/`engines/`/`grading.py`/`scheduler.py`），建 `tests/unit/` 下 OCR 测试文件，验证空包可导入、`pytest --collect-only` 收集到用例
- [x] 1.2 建 `tests/unit/conftest.py` 或复用既有 `db_session` fixture，验证 OCR 单元测试能复用内存库会话

## 2. UploadSession 状态机

- [x] 2.1 实现 `state_machine.py` 纯函数 `transfer(session_status, target)`：10 态合法迁移 + 终态守卫（对 `done`/`discarded`/`error` 做变更抛异常）+ 版本递增，验证 `tests/unit/test_ocr_state_machine.py` 全绿（覆盖状态转移、终态守卫、非法迁移拒绝）

## 3. 百度 Auth Token 管理

- [x] 3.1 实现 `token.py`：内存缓存复用 + 300s 安全边距（距到期 ≤300s 触发 `client_credentials` 刷新）+ 凭据缺失报错，验证 `tests/unit/test_baidu_token.py` 覆盖缓存复用、安全边际刷新、凭据缺失三种路径

## 4. 引擎协议、路由与降级链

- [x] 4.1 定义 `engines/` 统一 `OCRProvider` 协议（`extract(document) → 结构化结果`）与文件类型判定，验证协议类型标注可被路由引用
- [x] 4.2 实现 `baidu_engine`：`doc_analysis` 同步调用 + 学号/姓名正则提取（8-11 位学号、`unknown`/`待识别` 兜底）+ 未识别到有效文本（<10 字符）标记部分结果，验证 `tests/unit/test_ocr_engines.py` mock httpx 通过
- [x] 4.3 实现 `vlm_fallback`：复用现有 LLM fallback 通道（langchain-openai），验证 mock LLM 返回降级识别结果且 `fallback_used=True`
- [x] 4.4 实现 `mineru_engine` 可用性检测占位：`try-except` 导入 + `check_available()` 返回 False 时标记不可用并降级，验证 mock 环境下 PDF 解析降级到百度+Vision 不抛未捕获异常
- [x] 4.5 实现双引擎路由：图片→百度、PDF→MinerU，每层失败才进下一层（图片：百度→VLM→部分结果；PDF：MinerU→百度→VLM），成功即返回，验证路由与降级链测试覆盖逐层推进与成功短路

## 5. LLM 批改引擎

- [x] 5.1 实现答案来源选择：题库匹配（有考试按题号取标准答案）> 教师录入 > LLM 自判，验证 `tests/unit/test_llm_grading.py` 覆盖三模式优先级
- [x] 5.2 实现答案标准化比较：去首尾空白 + 转大写后相等才正确；空答案/空标准答案/AUTO 判错，验证比较函数边界用例
- [x] 5.3 实现自判安全模式：无法确定答案时标记需人工复核、不自动判对错，验证自判模式输出带人工复核标记
- [x] 5.4 实现主观题 LLM 语义批改（同步调用 LLM 判定 + 结构化解包），验证 mock LLM 返回批改结果并容错解析失败

## 6. OCRTask 调度与重试

- [x] 6.1 实现 `scheduler.py`：APScheduler interval 5s 轮询 `pending → processing → done/failed`（成功置 progress 100、失败记 error），验证 `tests/unit/test_ocr_scheduler.py` 模拟轮询周期覆盖状态流转
- [x] 6.2 实现重试：`failed → pending` + 清空错误信息与识别结果，验证重试幂等（重复重试不产生副作用）

## 7. 批改判卷 API

- [x] 7.1 实现 `api/v1/ocr.py`：批量上传（空文件 400）、批次状态聚合（total/done/failed/pending）、任务重试、教师任务列表，验证 `tests/integration/test_ocr_api.py` 状态码与结构
- [x] 7.2 实现触发批改（无 done 任务 404）、保存结果（分档落库：模式1 展开 `StudentAnswer` + 模式2/3 只落 `StudentSubmission`）、查询批改结果，验证集成测试断言分档落库与未注册学生跳过
- [x] 7.3 实现服务可用性检查端点（三引擎 available 状态），验证启动时返回 ocr/mineru/vision 状态结构
- [x] 7.4 在 `main.py` 注册 `ocr_router`，验证 `/api/ocr/*` 路由可达且与其他路由无冲突

## 8. 诊断接线与收尾

- [x] 8.1 保存结果成功后触发既有 `run-llm` 批量诊断（按 exam_id），验证 mock 下保存后诊断被调用且仅一次
- [x] 8.2 全量 `pytest tests/ --tb=short -x` 通过，验证无回归
- [x] 8.3 运行 `/code-review` 后端审查并修复问题，验证审查通过、`git log` 按 Conventional Commits 原子提交
