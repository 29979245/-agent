"""周报 LLM 生成服务（文档 33 §7 通俗化）。

与 llm_diagnosis.py 同构：按 §7 模板构建 prompt（术语转换表/输出约束/字数限制
嵌入 system prompt）→ FallbackDiagnosisLLMClient.complete → extract_json →
解析 {summary, detail, advice, no_data} → 解析失败纠错重试 ≤3 → 降级错误信号。
ai-summary 复用同一 prompt/解析基建，仅 user prompt 差异。
"""
import datetime
import json

from sqlalchemy.orm import Session

from app.db.models import Question, Student
from app.services.analytics.report_service import (
    week_answers,
    week_start,
    week_stats,
)
from app.services.diagnosis.aggregation import normalize_profile
from app.services.diagnosis.llm_diagnosis import (
    DiagnosisLLMError,
    FallbackDiagnosisLLMClient,
    extract_json,
)
from app.services.question.serializers import split_knowledge_points

WEEKLY_FIELDS = ("summary", "detail", "advice", "no_data")
PARSE_ATTEMPTS = 3

# 文档 33 §7 术语转换表（system prompt 强制约束）
TERM_TABLE = [
    ("氧化还原反应", "物质与氧气反应/电子的转移"),
    ("盐类水解", "某些盐溶于水后改变水的酸碱性"),
    ("勒夏特列原理", "化学反应平衡的移动规律"),
    ("电解质溶液", "能导电的溶液"),
    ("配平化学方程式", "让方程式两边原子数量相等"),
    ("离子反应", "带电粒子之间的反应"),
    ("物质的量", "化学中计数微粒数量的单位"),
    ("化学键", "原子之间的连接方式"),
    ("摩尔质量", "每摩尔物质的质量"),
    ("阿伏加德罗常数", "1摩尔物质中包含的微粒数量"),
]


class WeeklyReportError(RuntimeError):
    """周报生成失败（全部 Provider/解析重试耗尽）。"""


def _term_table_block() -> str:
    return "\n".join(f'- "{t}" → "{c}"' for t, c in TERM_TABLE)


def build_weekly_report_system_prompt() -> str:
    """§7 System Prompt：角色 + 核心规则 + 术语表 + 输出格式约束。"""
    return "\n".join(
        [
            "你是 ChemAI 的家长助手。你的任务是将学生的化学学习数据转化为家长（40-55 岁，无化学专业背景）能理解的周报。",
            "",
            "核心规则：",
            '1. 永远不提及排名、不与其他学生比较、不使用"落后""差"等负面词汇',
            "2. 化学专业术语必须转换为日常用语（参见术语转换表）",
            "3. 先肯定进步（至少 1 条），再给出 1-2 条具体的家庭配合建议",
            "4. 总字数控制在 150-250 字之间",
            "5. 语气温暖、鼓励，让家长获得方向感而非焦虑感",
            '6. 如果当周没有练习数据，说明"本周暂无练习记录"，不编造内容',
            "",
            "术语转换表（必须遵守）：",
            _term_table_block(),
            "",
            "输出格式约束：",
            "- 第一段：概述（1-2 句，先说本周练习概况）",
            "- 第二段：具体表现（2-3 句，结合数据描述掌握情况，使用日常语言）",
            "- 第三段：家庭建议（1-2 句，具体可操作的建议）",
            "- 如果当周无数据，仅输出一段简短说明",
            "",
            '仅输出一个 JSON 对象（不要输出任何额外文字或 markdown 围栏），格式：',
            '{"summary": "概述（≤60字）", "detail": "具体表现（≤120字）", '
            '"advice": "家庭建议（≤80字）", "no_data": false}',
        ]
    )


_BARRIER_LABELS = {"concept": "概念理解", "reading": "审题障碍", "expression": "表述障碍"}
_BARRIER_STYLE = {
    "concept": "概念理解还不够扎实，需要多从原理层面理解",
    "reading": "读题时偶尔会漏看关键条件，审题习惯需要加强",
    "expression": "思路通常是对的，化学用语和规范书写需要加强",
}


def _dominant_barrier(profile: dict) -> str:
    """障碍画像占比最高的障碍键；全零/异常默认 concept。"""
    norm = normalize_profile(profile)
    return max(norm, key=norm.get) if any(v > 0 for v in norm.values()) else "concept"


def _barrier_distribution(profile: dict) -> str:
    """障碍类型分布文本，如「概念理解 70%、审题障碍 20%」；无画像返回「暂无细分数据」。"""
    norm = normalize_profile(profile)
    parts = [f"{_BARRIER_LABELS[k]} {int(round(v * 100))}%" for k, v in norm.items() if v > 0]
    return "、".join(parts) if parts else "暂无细分数据"


def _learning_characteristics(barrier: str, weekly_accuracy: float) -> str:
    """由主导障碍 + 本周正确率拼出的学习特点描述。"""
    base = _BARRIER_STYLE.get(barrier, _BARRIER_STYLE["concept"])
    if weekly_accuracy >= 0.8:
        return f"本周正确率不错，{base}"
    return base


def _count_change(this: int, last: int) -> str:
    return f"{this - last:+d}（上周 {last} 次，本周 {this} 次）"


def _accuracy_change(this: float, last: float) -> str:
    this_pct = int(round(this * 100))
    last_pct = int(round(last * 100))
    return f"{this_pct - last_pct:+d}%（上周 {last_pct}%，本周 {this_pct}%）"


def _collect_weekly_data(db: Session, student: Student, today: datetime.date) -> dict:
    """本周练习数据聚合：次数/题数/正确数/正确率/薄弱知识点/障碍分布/周对比。"""
    monday = week_start(today)
    sunday = monday + datetime.timedelta(days=6)
    this = week_stats(db, student.id, monday, today)
    last = week_stats(
        db, student.id, monday - datetime.timedelta(days=7), monday - datetime.timedelta(days=1)
    )

    answers = week_answers(db, student.id, monday, today)
    # 薄弱知识点：按错误频率排序 Top 3
    err_count: dict[str, int] = {}
    qids = {a.question_id for a in answers}
    kp_map = {
        q.id: split_knowledge_points(q.knowledge_points)
        for q in db.query(Question).filter(Question.id.in_(qids)).all()
    }
    for a in answers:
        if a.is_correct:
            continue
        for kp in kp_map.get(a.question_id, ()):
            err_count[kp] = err_count.get(kp, 0) + 1
    weak = [f"{kp}(错{n}次)" for kp, n in sorted(err_count.items(), key=lambda kv: -kv[1])[:3]]

    profile = student.barrier_profile or {}
    barrier = _dominant_barrier(profile)

    return {
        "student_name": student.name,
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "practice_count": this["practice_count"],
        "total_questions": this["total_questions"],
        "correct_count": this["correct_count"],
        "accuracy_percent": round(this["accuracy"] * 100, 1),
        "weak_knowledge_points_list": ", ".join(weak) if weak else "无",
        "learning_characteristics": _learning_characteristics(barrier, this["accuracy"]),
        "barrier_distribution": _barrier_distribution(profile),
        "practice_count_change": _count_change(this["practice_count"], last["practice_count"]),
        "accuracy_change": _accuracy_change(this["accuracy"], last["accuracy"]),
        "has_data": this["total_questions"] > 0,
    }


def build_weekly_report_prompt(data: dict) -> list[dict]:
    """组装周报 prompt（§7 模板：system + user）。"""
    user = (
        "以下是 {student_name} 本周（{week_start} 至 {week_end}）的学习数据，请生成本周学习周报。\n\n"
        "本周练习数据：\n"
        "- 完成练习次数：{practice_count} 次\n"
        "- 总练习题数：{total_questions} 题\n"
        "- 正确题数：{correct_count} 题\n"
        "- 正确率：{accuracy_percent}%\n\n"
        "薄弱知识点（按错误频率排序）：\n{weak_knowledge_points_list}\n\n"
        "学习特点（基于最近诊断）：\n{learning_characteristics}\n\n"
        "障碍类型分布：\n{barrier_distribution}\n\n"
        "周度对比（与上周比较）：\n"
        "- 练习次数变化：{practice_count_change}\n"
        "- 正确率变化：{accuracy_change}\n"
    ).format(**data)
    return [
        {"role": "system", "content": build_weekly_report_system_prompt()},
        {"role": "user", "content": user},
    ]


def parse_weekly_report_response(text: str) -> dict:
    """解析 LLM 输出为 {summary, detail, advice, no_data}。无效响应抛 ValueError。"""
    if not text or not text.strip():
        raise ValueError("周报响应为空")
    raw = extract_json(text)
    if raw is None:
        raise ValueError("响应中未找到 JSON 对象")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层非对象")
    no_data = bool(data.get("no_data"))
    if "summary" not in data:
        raise ValueError("缺 summary 字段")
    return {
        "summary": str(data["summary"]),
        "detail": str(data.get("detail", "")),
        "advice": str(data.get("advice", "")),
        "no_data": no_data,
    }


def _corrective_prompt(messages: list[dict], error: str) -> list[dict]:
    return messages + [
        {
            "role": "user",
            "content": (
                f"你上一次的输出无法解析：{error}。请重新输出，"
                '只包含一个严格 JSON 对象，字段为 summary/detail/advice/no_data。'
            ),
        }
    ]


def generate_weekly_report(
    db: Session,
    student_id: int,
    today: datetime.date | None = None,
    client=None,
) -> dict:
    """生成当周周报；无数据返回 no_data 分支；LLM 失败抛 WeeklyReportError。"""
    student = db.get(Student, student_id)
    if student is None:
        raise WeeklyReportError("学生不存在")
    today = today or datetime.date.today()
    data = _collect_weekly_data(db, student, today)
    if not data["has_data"]:
        # no_data 分支：不调用 LLM，仅 summary 说明
        return {"summary": "本周暂无练习记录", "detail": "", "advice": "", "no_data": True}

    client = client or FallbackDiagnosisLLMClient()
    messages = build_weekly_report_prompt(data)
    last_error = "周报生成失败"
    for attempt in range(PARSE_ATTEMPTS):
        try:
            raw = client.complete(messages)
        except DiagnosisLLMError as e:
            raise WeeklyReportError(f"周报生成失败：{e}") from e
        try:
            return parse_weekly_report_response(raw)
        except ValueError as e:
            last_error = str(e)
            if attempt < PARSE_ATTEMPTS - 1:
                messages = _corrective_prompt(messages, str(e))
    raise WeeklyReportError(f"周报生成失败：{last_error}")


def get_or_generate_weekly_report(
    db: Session,
    student_id: int,
    today: datetime.date | None = None,
    client=None,
) -> dict:
    """周报查询/生成入口（design D6 周内去重）。

    weekly_report_week == 本周周一 则返回缓存；否则生成并覆写存储。
    生成失败抛 WeeklyReportError，不写入（周报保持未生成状态以便重试）。
    """
    student = db.get(Student, student_id)
    today = today or datetime.date.today()
    monday = week_start(today)
    if student is not None and student.weekly_report_week == monday and student.weekly_report:
        return dict(student.weekly_report)
    report = generate_weekly_report(db, student_id, today=today, client=client)
    student.weekly_report = report
    student.weekly_report_week = monday
    db.commit()
    return report


# ---------------- AI 通俗解读（复用同一基建，2.6） ----------------

def build_ai_summary_prompt(data: dict) -> list[dict]:
    """ai-summary 的 user prompt：家长视角解读子女报告，仅 user prompt 与周报不同。"""
    stats = data.get("stats", {})
    kp_list = ", ".join(
        f"{k['name']}（{'掌握不错' if k['mastery'] >= 0.7 else '需要加强'}）"
        for k in data.get("knowledge_points", [])
    ) or "无"
    user = (
        "以下是一名学生的化学学习概况，请用家长能听懂的话，给出一段通俗解读。\n\n"
        "统计卡：\n"
        "- 本周练习次数：{weekly_exercises} 次\n"
        "- 本周正确率：{weekly_accuracy}%\n"
        "- 连续学习天数：{streak_days} 天\n"
        "- 累计练习总量：{total_practices} 次\n\n"
        "知识掌握概览（名称:掌握度0-1）：\n{kp_list}\n\n"
        "要求：语气温暖，先肯定后给建议，不制造焦虑，不提排名，不与其他学生比较，"
        "化学术语换成日常说法。一段话 100-150 字，只输出一个 JSON 对象："
        '{{"summary": "通俗解读"}}'
    ).format(
        weekly_exercises=stats.get("weekly_exercises", 0),
        weekly_accuracy=round(stats.get("weekly_accuracy", 0) * 100, 1),
        streak_days=stats.get("streak_days", 0),
        total_practices=stats.get("total_practices", 0),
        kp_list=kp_list,
    )
    return [
        {"role": "system", "content": build_weekly_report_system_prompt()},
        {"role": "user", "content": user},
    ]


def generate_ai_summary(
    db: Session,
    student_id: int,
    report: dict,
    today: datetime.date | None = None,
    client=None,
) -> dict:
    """对家长报告生成通俗解读；LLM 失败抛 WeeklyReportError。"""
    client = client or FallbackDiagnosisLLMClient()
    messages = build_ai_summary_prompt(report)
    last_error = "解读生成失败"
    for attempt in range(PARSE_ATTEMPTS):
        try:
            raw = client.complete(messages)
        except DiagnosisLLMError as e:
            raise WeeklyReportError(f"解读生成失败：{e}") from e
        try:
            parsed = parse_weekly_report_response(raw)
            return {"summary": parsed["summary"]}
        except ValueError as e:
            last_error = str(e)
            if attempt < PARSE_ATTEMPTS - 1:
                messages = _corrective_prompt(messages, str(e))
    raise WeeklyReportError(f"解读生成失败：{last_error}")
