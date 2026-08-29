## MODIFIED Requirements

### Requirement: 工具切片——出题组
系统 SHALL 提供出题组 7 个工具：`search_exam_bank`、`web_search`、`show_exam_workbench`、`generate_questions`、`save_to_bank`、`list_banks`、`delete_bank`；各工具 SHALL 遵循 doc 30 §3.2 的输入/限次/角色约束，限次定义于 TOOL_META；出题组依赖联网的工具 SHALL 通过 `ToolContext.search` 注入独立搜索客户端（与 LLM Provider 解耦）。

`search_exam_bank` SHALL 提供三级搜索：本地关键词→向量召回→联网补齐；SHALL 支持可选的 `source / region / knowledge_point` 结构化过滤；ChromaDB 向量命中 SHALL 按相似度 ≥ 0.6 入列（低于阈值不入列），ChromaDB 不可用降级为关键词匹配的命中无相似度、不适用阈值（doc 25 §7.4）；本地结果不足 3 条且含 keyword 时 SHALL 触发联网补齐，结果标记"AI辅助搜索（本地题库仅 N 道，以下为AI补充）"；命中含图片字段（`image_url`）的题目时 SHALL 发射 `exam_images` SSE 事件携带图片 URL；题目图片字段由题目数据源（导入/上传）提供，数据源未携带图片字段时该事件不触发。

`web_search` SHALL 执行多路联网搜索并将结果摘要至 400 字内；搜索服务未配置时 SHALL 返回明确降级提示（如"未配置"），不得抛错或中断对话。

`show_exam_workbench` SHALL 内联渲染出题工作台面板，返回参数 SHALL 采用 `types:[{val, active, qty}]` 数组结构（val 为题型值、active 为是否激活、qty 为生成数量），并携带 knowledge_points / difficulty / source / bank_name。

`generate_questions` SHALL 依次执行 RAG 检索→LLM 生成→化学式标准化→完整四维审核；化学式标准化 SHALL 将裸化学式（如 H2O、NaCl）数字下标转换为 LaTeX 并包装为 `$...$`，箭头统一替换为 `\rightarrow` / `\rightleftharpoons`；四维审核 SHALL 覆盖方程配平、反应条件、产物、分子结构，每维返回判定（passed/warning/failed/blocked）与 evidence；仅系数配平失败（blocked）SHALL 判定不可保存，条件/产物/结构问题 SHALL 标记为 warning 不阻断保存（软标记，doc 26 §6.3）；生成题目 SHALL 携带陷阱提示 `trap_hint` 与 RAG 元数据 `is_from_rag / source_question_id / similarity / match_method`；提示词 SHALL 按检索真题数分支——命中 ≥3 条走模式 A（以真题为风格参考）、否则模式 B（纯生成）。

`save_to_bank` SHALL 创建题库文件夹、逐题入库并同步向量索引；成功返回 SHALL 携带三段式跳转协议 `{navigate, page:"exam-v2", populate, actions:[{action:"openTab", payload:"bank"}]}`，其中 populate 含 set_id / set_name。

`list_banks` SHALL 列出题库文件夹，返回 id / name / question_count / is_preset。

`delete_bank` SHALL 删除题库文件夹及其题目关联，题目实体保留，需审批。

#### Scenario: 三级搜索兜底标记
- **WHEN** 本地题库结果不足 3 条且含 keyword
- **THEN** 触发联网补齐，结果标记"AI辅助搜索（本地题库仅 N 道，以下为AI补充）"

#### Scenario: 化学式标准化
- **WHEN** generate_questions 生成含裸化学式（如 H2O、NaCl）的题目
- **THEN** 数字下标转换为 LaTeX 格式并包装为 $...$，箭头统一替换为 \rightarrow / \rightleftharpoons

#### Scenario: 删除题库需审批
- **WHEN** Agent 调用 delete_bank
- **THEN** 进入审批流程，未确认不执行删除

#### Scenario: 联网搜索未配置降级
- **WHEN** 搜索客户端未配置（ToolContext.search 为空）而 Agent 调用 web_search 或三级搜索的 Tier-3
- **THEN** 返回明确降级提示（"未配置"），对话正常继续，不抛错

#### Scenario: 向量阈值过滤
- **WHEN** 向量召回结果的相似度低于 0.6
- **THEN** 该结果不入列，不计入可用真题数

#### Scenario: 四维审核阻断
- **WHEN** 生成的题目系数配平失败（blocked）
- **THEN** 该题标记为不可保存并给出 balance 维度 evidence，不得带病入库；条件/产物/结构问题软标记 warning 不阻断（doc 26 §6.3）

#### Scenario: RAG 元数据回传
- **WHEN** generate_questions 基于检索到的真题生成题目
- **THEN** 返回题目携带 is_from_rag / source_question_id / similarity / match_method 元数据

#### Scenario: 工作台 types 数组
- **WHEN** Agent 调用 show_exam_workbench
- **THEN** 返回含 types 数组（val/active/qty）的面板参数，前端按数组渲染多题型生成

#### Scenario: 保存三段式跳转
- **WHEN** save_to_bank 成功入库
- **THEN** 返回 navigate(page="exam-v2") + populate(含 set_id/set_name) + actions(openTab=bank) 三段协议

#### Scenario: exam_images 事件
- **WHEN** search_exam_bank 命中的题目含图片字段（image_url）
- **THEN** 发射 exam_images SSE 事件携带图片 URL，供前端渲染；题目数据源未携带图片字段时不触发
