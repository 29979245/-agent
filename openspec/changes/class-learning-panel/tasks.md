## 1. 学情聚合服务层（services/analytics/）

- [x] 1.1 实现知识点错误率聚合纯函数（E=errors/total，一题多知识点分别计，total=0 排除不参与排名），编写 L1 单元测试覆盖一题多 KP、total=0、空作答
- [x] 1.2 实现班级均分指数衰减加权（w=exp(-ln2·Δt/604800)，一周前权重 50%），编写 L1 单元测试覆盖衰减曲线与单次异常低分不拉垮
- [x] 1.3 实现障碍分布主导计数（normalize_profile + dominant，画像缺失/全零不计入），编写 L1 单元测试覆盖缺失/全零/常规画像
- [x] 1.4 实现 ClassLearningPanel 组装（knowledge_points 按错误率降序 top10、top_errors top5、barrier_distribution 整数人数、avg_score_trend 最近 10 次升序），编写 L1 单元测试覆盖排序与截断边界

## 2. /api/panel 端点

- [x] 2.1 实现 `GET /api/panel/class/{class_id}` 面板完整数据，编写 L2 集成测试覆盖有数据与无作答空数据两种场景
- [x] 2.2 实现 `GET /api/panel/class/{class_id}/knowledge/{knowledge_point}` 按知识点错误率分布与出错学生，编写 L2 集成测试
- [x] 2.3 实现 `GET /api/panel/class/{class_id}/student/{student_id}` 学生学情详情，编写 L2 集成测试覆盖跨班学生 404
- [x] 2.4 实现 `GET /api/panel/class/{class_id}/trend` 班级成绩趋势，编写 L2 集成测试覆盖有数据与空趋势返回空数组
- [x] 2.5 实现 `GET /api/panel/dashboard/{teacher_id}` 教师首页概览，编写 L2 集成测试覆盖跨教师越权 403
- [x] 2.6 实现 `GET /api/panel/export/{class_id}` PDF 导出（复用 paper-export 栈），编写 L2 集成测试覆盖文件生成与不含他班数据

## 3. 班级学生列表端点

- [x] 3.1 实现 `GET /api/classes/{class_id}/students`（id/name/barrier_profile），编写 L2 集成测试覆盖班级不存在 404

## 4. 权限与隔离

- [x] 4.1 面板端点统一 `analysis/read` + 显式拒绝 student，编写 L2 集成测试覆盖学生角色访问任意面板端点返回 403
- [x] 4.2 班级范围隔离（教师仅本校 school_id，admin+ 不限），编写 L2 集成测试覆盖教师访问他校班级 403/404

## 5. 接线与回归

- [x] 5.1 在 `main.py` 注册 panel 路由与 classes students 端点，编写冒烟测试验证应用可启动且路由可达
- [x] 5.2 全量回归 `pytest tests/ --tb=short -x` 通过，既有 exam/diagnosis 测试无回归
- [x] 5.3 运行 `graphify update .` 保持知识图谱同步
