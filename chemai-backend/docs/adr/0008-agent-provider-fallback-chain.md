# Agent LLM Provider 回退链定为 MiMo→qwen→deepseek，偏离 .env 默认（deepseek 主）

Agent 阶段（phase-6）决策：LLMClient 三级回退链按 **MiMo-V2.5（主）→ 通义 qwen-turbo → DeepSeek-V4-Flash（兜底）**（doc 38 §2.3 名义顺序），`.env` 的 `LLM_PROVIDER` 从当前 `deepseek` 改为 `mimo`。默认推理与视觉消息都先走 MiMo，qwen/deepseek 纯做故障降级。

**Why**：
1. **MiMo 是唯一同时具备视觉 + 联网搜索 + 化学满分（20/20）的模型**——doc 38 §2.2 实测三模型化学准确率并列 20/20，但只有 MiMo 支持视觉与联网。放主位后 doc 30 §6.2 的"图片消息走视觉模型"能力路由被主链路吸收，Gateway 的 provider 选择逻辑可简化。
2. **化学最强 + 延迟/成本并非主位依据**——DeepSeek 实测最快（2.1s）最便宜，但无视觉/联网，作兜底是为覆盖能力面而非按性能排序。若未来出现"推理专用 + 视觉专用"的双模型分工，此链可再调整。
3. **偏离 `.env` 现状是刻意为之**——若不记录，后人看到 `LLM_PROVIDER=deepseek` 更"合理"（最快最省）会把主位改回 deepseek，破坏视觉/联网能力覆盖。

**调和方式**：`app/agents/factories/model_factory.py` 维护 provider 配置字典（model + base_url），LLMClient 按链降级，每级 3 次指数退避（1s→2s→4s）；`.env` 需配置 `LLM_PROVIDER=mimo` 与 `MIMO_API_KEY`/`QWEN_API_KEY`。未识别 Provider 标识触发启动异常，阻止静默错误配置。

**后果**：MiMo 主位平均延迟 3.5s（vs deepseek 2.1s），首 token P95 目标 <3s 需监控；若实测超目标，回退链前移 deepseek 是配置级改动，不影响工具层。
