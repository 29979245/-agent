## MODIFIED Requirements

### Requirement: 题库管理（Tab 2）
系统 SHALL 在题库管理 Tab 提供左侧文件夹目录（系统预设置顶 + 当前教师自己的文件夹并集）、region/year 组合筛选条、题目卡片缩略网格展示与批量操作，题库与题目数据来自真实后端接口。

#### Scenario: 查看题库题目
- **WHEN** 教师选择一个题库文件夹
- **THEN** 显示该题库下题目卡片缩略网格（题干经 KaTeX 渲染、答案、知识点标签、审核状态角、题型标签与难度徽章）

#### Scenario: 新建与删除题库
- **WHEN** 教师点击新建或删除题库
- **THEN** 题库目录更新并反映到文件夹选择器；系统预设文件夹删除被拒并提示

#### Scenario: 目录含预设与我的
- **WHEN** 文件夹目录加载
- **THEN** 系统预设文件夹（is_preset）置顶展示，当前教师自己的文件夹随后展示

#### Scenario: 按 region/year 组合筛选
- **WHEN** 教师按 region/year 组合条件筛选题库
- **THEN** 目录仅显示匹配文件夹，并按分页返回 total/page/page_size

#### Scenario: 批量操作题目
- **WHEN** 教师勾选多道题目
- **THEN** 可批量加入考试或从题库移除，操作后卡片网格刷新

### Requirement: 考试列表与生命周期（Tab 4）
系统 SHALL 在考试列表 Tab 提供创建考试、考试卡片列表（含六态状态标签）与按状态条件显示的操作菜单，考试数据来自真实后端接口。

#### Scenario: 创建考试
- **WHEN** 教师填写考试名称与班级并提交
- **THEN** 创建新考试并出现在考试列表

#### Scenario: 状态标签与操作菜单
- **WHEN** 考试卡片显示状态标签
- **THEN** 草稿（Draft）/已发布（Published）/进行中（InProgress）/阅卷中（Grading）/已完成（Completed）/已归档（Archived）各有对应标签；操作菜单按状态显示：草稿=编辑/发布/删除；已发布与进行中=开始阅卷/导出；阅卷中=完成统计/导出；已完成=归档/导出/看结果；已归档=导出（只读）

#### Scenario: 删除仅限草稿
- **WHEN** 考试非草稿状态
- **THEN** 操作菜单不显示删除操作

## ADDED Requirements

### Requirement: 批量导入题目到题库文件夹
系统 SHALL 支持向题库文件夹批量导入题目，通过 `POST /api/exam-bank/exam-sets/{set_id}/import-questions` 提交题目 ID 列表，并展示导入结果（新增/跳过）。

#### Scenario: 批量导入成功
- **WHEN** 教师勾选多道题并执行批量导入到目标文件夹
- **THEN** 系统返回导入统计（added/skipped），刷新文件夹题目数与该文件夹卡片网格

#### Scenario: 重复导入跳过
- **WHEN** 导入列表包含已在该文件夹的题目或不存在题目
- **THEN** 跳过并计入 skipped，不重复创建关联

### Requirement: 出题结果保存到题库文件夹
系统 SHALL 在出题工作台（Tab 1）提供「保存到题库文件夹」选择，批准入库时将已生成的题目导入所选文件夹。

#### Scenario: 批准入库并保存到文件夹
- **WHEN** 教师选定目标文件夹后批准入库
- **THEN** 题目通过批准接口入库，并导入到所选文件夹，展示保存结果

### Requirement: 编辑考试题目
系统 SHALL 在考试列表 Tab 的「编辑」操作提供抽屉弹窗，列出考试当前题目并支持加入与移除题目。

#### Scenario: 查看与加入题目
- **WHEN** 教师打开编辑抽屉
- **THEN** 显示考试当前题目列表，可从题库或历史真题选择题目加入考试

#### Scenario: 移除题目
- **WHEN** 教师在编辑抽屉移除一道题
- **THEN** 该题与考试解除关联，抽屉列表刷新

### Requirement: 考试试卷导出
系统 SHALL 在考试列表 Tab 提供「导出」操作，调用导出接口下载 Word 或 PDF 试卷，并可选是否含答案。

#### Scenario: 导出试卷文档
- **WHEN** 教师选择导出格式与是否含答案后触发导出
- **THEN** 下载对应格式的试卷文档
