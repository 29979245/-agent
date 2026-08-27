# OCR MVP 引擎接线：百度 OCR + LLM 语义批改一条链路先行，MinerU/correct_edu 留协议 stub

设计文档 24 §4 定义三引擎（百度/MinerU/VLM）+ §7.1 批改两路径（correct_edu 异步 / LLM 语义批改）。课件 51 号要求**双引擎路由**（PDF 路由 + 图片路由 + 降级链）真实实现，但依赖清单无 baidu/mineru/zhipu/xiaomi SDK，MinerU 需本地模型下载（Windows 磁盘不足时标记不可用），correct_edu 是异步任务+120s轮询+百度教育专属 API。本阶段决策：**端到端主链路走百度 doc_analysis（图片主场景）+ LLM 语义批改（同步）+ VLM 降级（复用现有 LLM fallback：MiMo→qwen→deepseek 走 langchain-openai）；引擎路由与降级链真实实现，MinerU 引擎做运行时可用性检测占位（try-except 导入 + check_available()，模型未下载则标记不可用、降级百度+Vision、测试全 mock），correct_edu 批改在统一协议下留 stub。**

**Why**：
1. **图片答题卡是设计文档定义的主场景**（手写用百度，§10.3），一条链路跑通端到端 MVP 即成立。
2. **VLM 兜底零新增依赖**——现有 `langchain-openai` + 三级 fallback 已是可用通道，降级链立即闭环。
3. **correct_edu 三重负担**（异步+轮询+专属 API），而客观题判定（设计 §11.4：大小写归一+字符串相等）本质是确定性比对，LLM 语义批改同步即可覆盖。
4. **协议抽象先行**——否则后接 MinerU/correct_edu 要返工。

**调和方式**：`services/ocr/engines/` 定义统一 `OCRProvider` 协议（`extract(document) → 结构化结果`），`provider` 配置切换（设计附录A `OCR_SHEET_PROVIDER`）。**引擎路由真实实现**：按文件类型路由（PDF→MinerU、图片→百度）+ 降级链（每层失败才进下一层）。`baidu_engine` 与 `vlm_fallback` 真实实现；`mineru_engine` 做运行时可用性检测占位（try-except 导入 + `check_available()`，模型未下载/磁盘不足则标记不可用并降级百度+Vision）；`baidu_correct_edu` 留 stub。

**后果**：MinerU 模型未下载或 Windows 磁盘不足时 PDF 解析标记不可用（降级百度 OCR + Vision，测试全 mock），correct_edu 批改暂不可用（stub 返回配置缺失提示，前端可见）；后续接入按协议补齐实现即可。correct_edu 上线后客观题判定仍以确定性比对为准，correct_edu 仅作并行补充。
