## 1. D3 — diagnose_barrier 家长班级门控

- [x] 1.1 在 `tools_diagnosis.py` 的 `diagnose_barrier` 顶部（班级分支前）加家长班级参数检查：`_user_role(ctx) == "parent"` 且携带 `class_id`/`class_name` 时抛 `ForbiddenError(detail="家长仅支持个体诊断")`；用单测断言 parent+class 返回 forbidden、parent+student 仍正常、teacher+class 正常
- [x] 1.2 运行 `pytest tests/unit/test_agent_tools_diagnosis.py -k "parent or barrier"` 确认新增家长门控用例通过且既有个体诊断用例不回归

## 2. D1 — 自适应练习 preview 路径

- [x] 2.1 `adaptive.py` 新增 `preview_batch(student_ids, count, ...)`：复用 `plan_for` + `sample_questions` 逐生生成预览 `{student_id, zpd_difficulty, difficulty, barrier, knowledge_points, weak_kps, question_count, shortfall, question_refs}`，不创建 `ExamRecord`、不复制题目；沿用批次限制 5 人；单测断言返回结构完整且 `ExamRecord` 表行数不增
- [x] 2.2 `tools_diagnosis.py` 的 `assign_adaptive_practice` 改为调用 `preview_batch` 并返回 `{results:[...], batch_limit, remaining}`，移除 `generate_batch` 写库调用；单测断言工具返回预览且无 DB 写入（`ExamRecord` 计数不变）
- [x] 2.3 运行 `pytest tests/unit/test_agent_tools_diagnosis.py -k adaptive` 及既有 `adaptive-practice-engine` 相关测试确认 preview 复用抽样逻辑不回归

## 3. D2 — 审批移至 API 确认落库

- [x] 3.1 `tool_meta.py` 移除 `assign_adaptive_practice` 的 `approval=True` 并将描述改为"生成个性化 ZPD 练习预览，确认后由前端调用 API 落库"；`guard.py` 的 `APPROVAL_TOOLS` 收窄为 `frozenset({"delete_bank"})`；单测断言该工具不再进入 `requires_approval_blocked`
- [x] 3.2 `adaptive.py` 新增 `persist_batch(items, name="自适应练习")`：校验每项 `student_id` 存在 + `question_refs` 为可复制题目，逐生创建 `ExamRecord`（`exam_type=practice`）并经 `copy_historical_question` 复制选中题目，返回 `{results:[{student_id, exam_id, practice_id, question_count}]}`；非法 refs 整批拒绝不落库；单测断言落库行数与复制题目正确
- [x] 3.3 `practice.py` 新增 `POST /api/practice/adaptive/confirm`：`@require_permission("practice", "create")`，请求 `{class_id, items:[{student_id, question_refs}]}` → 校验学生属于该班级 → `persist_batch` 落库返回逐生 `practice_id`；集成测试断言教师 200 落库、非教师 403、非法 refs 400/409 不落库
- [x] 3.4 运行 practice API 集成测试（含既有端点）确认 confirm 端点不回归既有 daily/submit/effect 流程

## 4. D4 — 前端最小接线（ai-tutor.html）

- [x] 4.1 `renderToolResult` 新增自适应预览分支：返回含 `results` 且每项含 `question_refs` 时渲染每生预览卡片（学生/难度/知识点/题数）+ "确认下发"按钮
- [x] 4.2 确认按钮点击收集 `items:[{student_id, question_refs}]` + `class_id` → `POST /api/practice/adaptive/confirm` → 成功后刷新/提示；手动浏览器验证预览渲染与确认落库闭环（本机 browse 被 Device Guard 封锁，改用集成测试验证 confirm 闭环）

## 5. 收尾验证

- [x] 5.1 全量跑 Agent 相关单测与 Evals：`pytest tests/unit -k agent` + `python -m app.evals.runners.run_evals --tier all --compare app/evals/baseline.json`，确认无回归、无能力劣化（threshold 0.05）
- [x] 5.2 按 CLAUDE.md 原子提交规范分 commit 提交（D3/D1/D2/D4 各自独立），`graphify update .` 保持知识图同步
