## Purpose

将考试题目导出为 Word/PDF 试卷：python-docx 按排版规格生成 A4 试卷（密封线、题型分节、化学式下标），HTML 报告转 PDF，支持学生版/教师版双版本且中文不乱码。

## ADDED Requirements

### Requirement: Word 试卷导出
系统 SHALL 将考试题目导出为 Word 文档，遵循排版规格（A4、上2.5/下2.0/左2.5/右2.0cm、正文 SimSun 11pt、标题 16pt 加粗居中、密封线区），并按题型分节渲染（选择题/填空题/计算题/实验题/推断题），化学式中的下标以上下标富文本呈现。

#### Scenario: 按题型分节导出
- **WHEN** 教师导出含多题型的考试
- **THEN** Word 文档按 选择题→推断题 顺序分节，每题含题号+题干，选择题含缩进选项，填空题保留空位标记，计算题留答题空白

#### Scenario: 化学式下标富文本
- **WHEN** 题目含 `H_2O` 等化学式
- **THEN** 导出文档中数字以下标渲染，而非普通字符

### Requirement: 学生版/教师版导出
系统 SHALL 依据 with_answers 参数导出两种版本：学生版不含答案；教师版以红色标注答案、绿色标注解析，并含"（含答案版）"标记。

#### Scenario: 学生版导出
- **WHEN** 教师以 with_answers=false 导出
- **THEN** 文档不含答案与解析

#### Scenario: 教师版导出
- **WHEN** 教师以 with_answers=true 导出
- **THEN** 文档答案红色标注、解析绿色标注，底部含"（含答案版）"

### Requirement: PDF 报告导出
系统 SHALL 将考试统计结果渲染为 HTML 报告（教师版/学生版）并转 PDF，含标题、统计卡（平均分/参考人数/题目数）、TOP5 高频错题表，教师版另含知识点错误分布，中文不乱码。

#### Scenario: 生成教师版报告
- **WHEN** 教师导出教师版 PDF 报告
- **THEN** 报告含班级统计卡、TOP5 错题表与知识点错误分布表

#### Scenario: 中文正常渲染
- **WHEN** 导出含中文字符的 PDF
- **THEN** 中文显示正常不出现方框（SimSun 已注册）
