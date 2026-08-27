## 1. 后端题型标注（has_options）

- [x] 1.1 在 `app/services/ocr/batch.py` 的 `run_grading` 内，批改后遍历 items，命中 `q_by_pos`（位置→Question）时补 `has_options=bool(q.options)`，写入 `task.result['grading']` 随任务落库。验证：新增单元/集成断言，`pytest tests/integration/test_ocr_api.py -k "has_options or grading"` 通过。
- [x] 1.2 补集成测试：模式1（有考试）`/api/grading/results/{id}` 的逐题 item 携带 `has_options` 且与题目 options 一致；模式2/3（无考试）不强制携带。验证：`pytest tests/integration/test_ocr_api.py` 全绿。

## 2. ChemAPI 扩展（frontend/pages/js/api.js）

- [x] 2.1 新增 multipart 上传方法 `uploadOcrBatch(files)`：FormData + Bearer，不设 Content-Type，复用 `handleAuth`/`extractDetail` 错误形状。验证：对未鉴权/空文件返回 401/400 可读提示。
- [x] 2.2 新增 7 个 JSON 方法：`getOcrBatchStatus`/`retryOcrTask`/`getOcrBatches`/`getOcrServicesStatus`/`runGrading`/`saveGrading`/`getGradingResults`，路径与载荷对齐 `app/api/v1/ocr.py`。验证：浏览器控制台逐个调用返回预期结构。

## 3. ocr.html 页面

- [x] 3.1 搭建 `frontend/pages/ocr.html` 骨架：页头（品牌 + 回链 `exam-v2.html`）、标题栏、自托管 `vendor/fonts` 字体 + 设计令牌（--oxford/--teal/--paper）、`ChemAuth.requireAuth()` + `isTeacherLike()` 角色守卫（越权跳 `forbidden.html?side=teacher`）。验证：非教师角色访问被重定向。
- [x] 3.2 上传区：拖拽 + 点击双通道，客户端校验格式（jpg/png/bmp/webp/pdf）、单文件 ≤10MB、单批 ≤10 张；上传调用 `uploadOcrBatch` 并进入进度区。验证：空文件/超限给出提示，合法上传创建批次。
- [x] 3.3 进度卡片区：`setInterval` 5s 轮询 `getOcrBatchStatus`，按 done/processing（进度条）/failed/pending 渲染；failed 显示重试入口（`retryOcrTask`）；终态或页面隐藏时停轮询。验证：上传后卡片逐张从等待→识别中→完成。
- [x] 3.4 班级→考试联动选择器 + 自动批改：选中考试后上传，批次 `total == done` 且非终态时一次性调用 `runGrading(batchId, examId)`，处理 404/409 可读提示。验证：OCR 完成后自动进入结果渲染。
- [x] 3.5 结果表格：`getGradingResults` 渲染姓名/学号/总分/选择题/填空题/状态/操作列；总分=正确数，选择题/填空题按 `has_options` 拆分（缺失则显示"—"）；`manual_review` 标记"需人工复核"徽章。验证：模式1 下选择题/填空题列有值。
- [x] 3.6 底部操作栏 + 保存：平均/最高/最低分由表格聚合；"导出成绩"（可留空导出或复用 `fetchBlob`，如无后端导出端点则暂以 CSV 前端生成）、"发送给学生"（占位提示）、"开始诊断障碍"占位链接 `diagnosis.html`；"保存"调 `saveGrading(batchId, examId)`，成功展示 saved/skipped/needs_review + 诊断触发状态并禁用按钮，错误体（BATCH_ALREADY_SAVED 等）直接提示。验证：保存后计数与诊断反馈正确展示。
- [x] 3.7 历史批次下拉：`getOcrBatches` 填充下拉，切换后加载该批次进度与结果。验证：下拉列出既有批次并可切换查看。

## 4. 验证

- [x] 4.1 后端回归：`pytest tests/`（或至少 `tests/unit/test_ocr_*.py` + `tests/integration/test_ocr_api.py`）全绿。验证：命令退出码 0。
- [x] 4.2 前端全闭环冒烟：启动后端，教师登录后打开 `ocr.html`，选择班级/考试，上传样例答题卡，走通 上传→OCR→自动批改→表格→保存 全链路并核对结果列。验证：浏览器可见完整流程无报错。（HTTP 级：新后端全链路 PASS，含 has_options 拆分、保存计数与诊断触发；页面 200 可访问、内联 JS 语法校验通过；无 headless 浏览器，像素级渲染未自动化）
