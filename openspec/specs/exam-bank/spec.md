## Purpose

为教师提供题库两级组织与检索：QuestionSet 文件夹 CRUD、文件夹-题目关联、真题库内存加载与历史真题查询，支撑出题工作台 Tab 2 题库管理闭环。

## Requirements

### Requirement: 题库文件夹创建与列表
系统 SHALL 提供题库文件夹（QuestionSet）创建与列表查询接口，列表支持按教师、地区、年份筛选并分页，返回每个文件夹的名称、题目数量、是否系统预设。

#### Scenario: 创建题库文件夹
- **WHEN** 教师提交文件夹名称（及可选地区/年份/描述）
- **THEN** 系统创建 QuestionSet 并返回其 ID，文件夹出现在列表

#### Scenario: 按条件筛选列表
- **WHEN** 教师按 region/year 组合条件查询题库列表
- **THEN** 系统返回匹配文件夹的分页结果，且每项含题目数量统计

### Requirement: 题库文件夹详情与删除
系统 SHALL 提供文件夹详情查询（含该文件夹全部题目）与删除接口；删除仅移除文件夹与题目的关联（QuestionSetItem），题目实体保留；系统预设文件夹不可删除。

#### Scenario: 查看文件夹详情
- **WHEN** 教师查询某文件夹详情
- **THEN** 系统返回文件夹信息及其关联题目列表（含题目内容、答案、知识点）

#### Scenario: 删除非预设文件夹
- **WHEN** 教师删除一个非系统预设文件夹
- **THEN** 文件夹及其 QuestionSetItem 关联被删除，但关联的题目实体仍存在

#### Scenario: 删除系统预设文件夹被拒
- **WHEN** 教师尝试删除 is_preset=true 的文件夹
- **THEN** 删除被拒绝并返回业务规则错误

### Requirement: 文件夹题目关联管理
系统 SHALL 支持向文件夹批量导入题目（import-questions）与从文件夹移除单题；移除仅解除关联、不删除题目。

#### Scenario: 批量导入题目
- **WHEN** 教师向文件夹批量提交题目 ID 列表
- **THEN** 系统为每道题创建 QuestionSetItem 关联并返回导入结果统计

#### Scenario: 移除单题
- **WHEN** 教师从文件夹移除某道题
- **THEN** 该题的 QuestionSetItem 关联被删除，题目实体保留

### Requirement: 真题库加载与历史真题查询
系统 SHALL 在启动时按 地区/年份/试卷 三层目录加载真题 JSON 到内存，并提供试卷树（papers）与历史真题分页查询（historical）接口。

#### Scenario: 启动加载真题库
- **WHEN** 系统启动读取真题库目录
- **THEN** 从 JSON 文件加载题目并输出加载数量日志，可被试卷树接口访问

#### Scenario: 试卷树浏览
- **WHEN** 教师查询试卷树
- **THEN** 系统返回 地区→年份→试卷 的层级结构

#### Scenario: 历史真题分页查询
- **WHEN** 教师按关键词/地区/年份分页查询历史真题
- **THEN** 系统返回匹配题目列表及分页信息
