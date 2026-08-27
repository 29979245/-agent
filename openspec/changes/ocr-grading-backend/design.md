## Context

现有代码只落地了 OCR 数据模型骨架：`UploadSession`/`OCRTask`/`StudentSubmission` 三实体 + 状态枚举（`UploadSessionStatus` 10 态、`OCRTaskStatus` 4 态），且已有模型单测。管线逻辑全部为空：`app/services/ocr/` 只有空引擎目录，`app/api/v1/` 无 ocr 路由。可复用基建：障碍诊断引擎（`services/diagnosis`，`run-llm` 按 exam_id 批量诊断）、LLM 三级 fallback（MiMo→qwen→deepseek / langchain-openai）、APScheduler（`exercise/scheduler.py` 的 `create_scheduler` 模式）、`config.py` 的 baidu keys / `ocr_provider`。约束：依赖零新增；无百度凭据时测试全 mock（课件 51 号 L19/L69）。

## Goals / Non-Goals

**Goals:**
- 跑通端到端批改判卷链路：批量上传 → OCR 识别 → LLM 批改 → 分档落库 → 触发诊断。
- 双引擎路由与降级链真实实现，MinerU 运行时可用性检测。
- 分档落库符合领域约束（ADR 0003），不污染题库与统计。
- 全部行为 TDD 可测（无凭据时 mock）。

**Non-Goals:**
- 前端两个页面（课件 51 号 Phase B，单独 change）。
- Agent 工具三件套（课件 58 号 Group B，前置 Agent 架构）。
- correct_edu 批改（本 change 留协议 stub）。
- MinerU 模型下载/部署（运行时检测不可用则降级）。
- UploadSession 题库导入/预览链（IMPORTING 分支，另一条业务线）。

## Decisions

**D1. UploadSession 状态机为纯函数**（`services/ocr/state_machine.py`）
`transfer(session_status, target)` → 终态守卫（对 `done`/`discarded`/`error` 做变更抛异常）+ 返回新状态 + 版本递增。替代方案：状态机逻辑散落在 service 方法内——终态守卫难独立测试。选纯函数便于 TDD（课件 51 号验证"状态转移 + 终态守卫 + 版本递增"）。

**D2. 批改结果分档落库**（ADR 0003）
模式1（有考试/题库匹配）展开逐题为 `StudentAnswer` 并触发诊断；模式2/3（无考试）只落 `StudentSubmission.answer_list`。否决"动态建题"：绕过题库审核链产生脏题。详见 `docs/adr/0003-ocr-grading-persistence-boundary.md`。

**D3. 引擎接线**（ADR 0004）
`services/ocr/engines/` 定义统一 `OCRProvider` 协议（`extract(document) → 结构化结果`），`provider` 配置切换（`OCR_SHEET_PROVIDER`）。`baidu_engine`（doc_analysis + Token 管理）与 `vlm_fallback`（复用 LLM fallback）真实实现；`mineru_engine` 运行时可用性检测占位（try-except 导入 + `check_available()`，不可用则降级百度+Vision）；`baidu_correct_edu` 留 stub。路由按文件类型 + 降级链每层失败才进下一层。详见 `docs/adr/0004-ocr-mvp-engine-wiring.md`。

**D4. 百度 Token 管理**（`services/ocr/token.py`）
模块级内存缓存 + 300s 安全边距（距到期 ≤300s 刷新，否则复用）+ 凭据缺失报错。async 单线程事件循环天然线程安全（设计文档 §4.4）。

**D5. LLM 批改**（`services/ocr/grading.py`）
答案来源选择（题库匹配 > 教师录入 > LLM 自判）→ 答案标准化比较（去空白 + 转大写 + 相等，确定性）→ 主观题可选走 LLM 语义判定。自判安全模式：无法确定答案标记需人工复核（对接 ADR 0003 模式3 落库）。

**D6. OCRTask 调度**（`services/ocr/scheduler.py`）
APScheduler interval 5s 轮询，复用 `exercise/scheduler.py` 的 `create_scheduler` 模式。轮询 `pending → processing → done/failed`；retry 端点 `failed → pending` + 清空 error 与 result。

**D7. 诊断接线（只读消费）**
保存结果成功后调用既有 `run-llm` 批量诊断接口（按 exam_id）。不重写诊断引擎，保证模式1 展开的 `StudentAnswer` 行（`barrier_type` 为 NULL）被既有链路拾取。

**D8. API 范围：批改判卷 8 端点**（路由取设计文档附录B，仅批改判卷线；`/api/ocr` 挂教师角色守卫）

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/ocr/tasks/batch` | 批量上传建批次（空文件 400） |
| GET | `/api/ocr/tasks/batch/{batch_id}` | 批次状态聚合（total/done/failed/pending） |
| POST | `/api/ocr/tasks/{task_id}/retry` | 重试失败任务（failed→pending） |
| GET | `/api/ocr/tasks` | 教师任务列表 |
| POST | `/api/grading/run` | 触发批改（无 done 任务 404） |
| POST | `/api/grading/save` | 保存结果（分档落库 + 触发诊断） |
| GET | `/api/grading/results/{batch_id}` | 查询批改结果 |
| GET | `/api/ocr/services/status` | 服务可用性检查 |

B 线（题库导入/预览/统计：upload/recognize/preview/confirm/stats/import/grade/cancel/parse）不在本 change，避免与 exam-bank 能力重叠。

## Risks / Trade-offs

- [百度 OCR 无凭据或限流] → mock 测试 + VLM 兜底降级 + 服务状态端点暴露配置缺失。
- [MinerU 模型不可用/磁盘不足] → `check_available()` 检测标记不可用，降级百度 OCR + Vision，前端可见。
- [主观题 LLM 判定可能幻觉] → 客观题确定性比较为主；自判模式标记人工复核，不自动判对错。
- [轮询放大端到端延迟] → 5s 间隔占总耗时 <20%（设计文档 §10.1），单教师场景可接受。
- [并发批改触发百度限流] → `asyncio.Semaphore(5)` 并发控制（设计文档 §10.4）。

## Migration Plan

无数据迁移：三实体模型已存在且已测试，本 change 只新增逻辑、协议与路由；如需补充字段走 alembic。变更在 `phase-4/business` 分支开发，回滚可 revert 提交，不触碰既有能力。

## Open Questions

- correct_edu 是否纳入后续 phase（58 号之后）：不影响本 change（协议 stub 即可），无需现在决定。
- 主观题触发 LLM 语义判定的具体题型边界：实现时在 grading 内定，不改变 spec 与任务拆分。
