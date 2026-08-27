## 阶段1：共享模块 + AI对话页

> 共享模块（student-common.js 登录守卫/4-tab、auth 角色分流）已在前序 `student-practice-frontend` 交付；本阶段交付 AI 助教页与其依赖的 API 封装与共享样式。

- [x] 1.1 api.js 新增 `getStudentReport` / `generateBindCode` / `changePassword` 真实端点封装（Bearer 注入），验证：console 调用返回后端对应数据
- [x] 1.2 api.js 新增 `agentChatStream` SSE 流式封装（fetch+ReadableStream、AbortController 取消、事件名别名 text/token 与 phase/thinking、错误回调带 `{degradable, kind}`），验证：mock SSE 源 phase/text/done 回调按序触发；404 触发 `onError({degradable:true, kind:'not_implemented'})`
- [x] 1.3 student.css 追加 AI 对话组件样式（`.drawer`+`.overlay`、`.bubble.ai`/`.bubble.user`、`.chip-row`、`.chat-input`、阶段标签、降级提示卡），验证：ai-tutor.html 引用后布局与原型 `stitch-prototypes/m/index.html` 一致
- [x] 1.4 ai-tutor.html 页面骨架：topbar（汉堡 + 「ChemAI 助教」+ 新对话）、抽屉（学生信息/历史对话占位/退出登录）、对话区、快捷芯片 5 个、输入区、`renderTabbar('ai-tutor')` + `requireStudent()` 守卫，验证：打开页面全分区渲染、tab「AI助教」激活
- [x] 1.5 抽屉交互：汉堡开/遮罩关、退出登录清会话回学生登录页，验证：手动流程
- [x] 1.6 发送消息与 SSE 流式渲染：追加用户气泡→AI 加载气泡→`agentChatStream` 流式填充（Markdown+KaTeX 渲染、XSS 净化）；phase 阶段标签（分析中/执行中/回复中）；done 结束加载态，验证：mock SSE 源逐 token 渲染、done 后输入框恢复
- [x] 1.7 快捷芯片与新建对话：芯片点击填入输入框；新建对话生成新 threadId 并清空回欢迎语，验证：手动流程
- [x] 1.8 降级路径：后端 404→「AI 助教即将上线」+重试入口；网络失败→「连接失败，点击重试」；流中断→「连接中断，点击重试」，验证：打 404 桩与关闭后端两种场景，输入框恢复、tab/抽屉不受影响

## 阶段2：练习页 + 错题本

> 前序 `student-practice-frontend` 已交付（practice.html、wrong.html），非本次范围，无新增任务。

## 阶段3：复习中心 + 个人报告页

> 复习中心已在前序交付；本阶段交付个人报告页（profile.html 我的页）。

- [x] 3.1 student.css 追加我的页组件样式（`.profile-card`/`.avatar`/`.bind-code`、`.stat-row` 三列、`.menu-row` 角标/chevron、`.sheet`+`.backdrop` 弹窗、设置表单），验证：profile.html 引用后布局与原型 `stitch-prototypes/m/report.html` 一致
- [x] 3.2 profile.html 页面骨架：topbar「我的」+设置齿轮、个人信息卡、学习统计、功能入口 5 项、`renderTabbar('profile')` + `requireStudent()` 守卫，验证：打开页面各分区渲染、tab「我的」激活
- [x] 3.3 报告数据加载：`getStudentReport` 填充姓名/班级/统计三列/学习计划（accuracy 归一化），验证：seed 学生数据正确显示；无记录学生统计 0、正确率 0%、计划空态
- [x] 3.4 绑定码交互：有码点击复制 toast「已复制」；无码点击生成并更新显示；重新生成覆盖旧码；生成失败 toast 不改显示，验证：手动点击流程与 6 位大写码显示
- [x] 3.5 学习周报弹窗：底部滑出（周标签、概览三数字、知识点掌握度条形图、教师评语 null 占位），验证：weekly 渲染；`knowledge_points` 为空时显示空态
- [x] 3.6 学习计划弹窗：`learning_plan` 有值渲染、为 null 显示空态，验证：两种数据形态显示正确
- [x] 3.7 修改密码：个人设置弹窗表单，必填 + 两次新密码一致校验，成功清会话回学生登录页、失败展示后端错误，验证：手动三种分支流程
- [x] 3.8 空态与错误态：报告加载失败 toast 提示、不展示错误数据、页面不崩溃，验证：关闭后端打开页面观察

## 集成验证

- [ ] 4.1 本地 uvicorn + seed 学生数据，浏览器走通我的页全流程（报告/绑定码/周报弹窗/改密码），验证：无 console 错误、数据与后端返回一致
- [ ] 4.2 AI 助教页降级 + mock 流式验证，回归 4-tab 导航与其他 4 页不回归，验证：页面切换与既有页面正常
- [ ] 4.3 运行 `pytest tests/ --tb=short -x`（后端未动应全绿）并 `graphify update .` 同步知识图谱，验证：命令通过
