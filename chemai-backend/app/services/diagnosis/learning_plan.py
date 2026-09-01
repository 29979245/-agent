"""学习计划规则引擎生成器 + 落库/通知服务（doc 22 §2.6 / doc 30 §3.3）。

生成器为纯规则引擎（诊断 LLM 仍是桩，不走 LLM）：复用 report_service 的
practice_answers / build_knowledge_points 计算知识点掌握度，薄弱知识点
（mastery < WEAK_THRESHOLD）按 mastery 升序取前 N 生成每日任务，结合障碍画像
主导轴输出针对性任务；无作答数据返回空计划 + 提示（端点映射 409）。

apply_plan 同时服务 REST 端点与 Agent 工具 send_learning_plan，保证同一
落库/通知行为不漂移。
"""
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.models import NotificationType, ParentNotification, Question, Student, StudentParentBinding
from app.services.analytics import report_service
from app.services.diagnosis.aggregation import BARRIER_LABELS, dominant_axis
from app.services.question.serializers import split_knowledge_points

WEAK_THRESHOLD = 0.6
MAX_WEAK = 3

_BARRIER_TASKS = {
    "concept": "回归教材梳理概念，用思维导图理清知识点间的逻辑关系",
    "reading": "重点练习审题类题目，圈划题目关键词后再作答",
    "expression": "重点练习表述类题目，规范化学用语与书写步骤",
}


def _knowledge_mastery(db: Session, answers: list) -> list[dict]:
    """按作答题目聚合各知识点掌握度（复用 report_service.build_knowledge_points）。"""
    qids = {a.question_id for a in answers}
    kp_map = {
        q.id: split_knowledge_points(q.knowledge_points)
        for q in db.query(Question).filter(Question.id.in_(qids)).all()
    }
    return report_service.build_knowledge_points(answers, kp_map)


def generate_plan(db: Session, student_id: int) -> dict:
    """规则引擎生成学习计划预览（不落库、不通知）。"""
    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError(detail="学生不存在", error_code="STUDENT_NOT_FOUND")
    answers, _ = report_service.practice_answers(db, student_id)
    if not answers:
        return {"empty": True, "message": "暂无足够学习数据"}

    dominant = dominant_axis(student.barrier_profile)
    barrier_task = _BARRIER_TASKS[dominant]

    knowledge_points = _knowledge_mastery(db, answers)
    weak = sorted(
        (kp for kp in knowledge_points if kp["mastery"] < WEAK_THRESHOLD),
        key=lambda kp: kp["mastery"],
    )[:MAX_WEAK]
    # 无薄弱点（或题目未标知识点）→ 按掌握度升序取前 N 做巩固，仍有兜底
    focus = weak or sorted(knowledge_points, key=lambda kp: kp["mastery"])[:MAX_WEAK]

    if not focus:
        days = [{"label": "第1天", "tasks": ["错题回顾 2 题", "综合练习 5 题"]}]
        return {
            "title": f"{student.name}的巩固练习计划",
            "plan_text": (
                "## 学习计划\n\n基于近期作答数据，建议完成错题回顾与综合练习，"
                f"巩固已学知识。当前主导障碍为「{BARRIER_LABELS[dominant]}」，"
                "请按每日任务逐步推进。"
            ),
            "plan_data": {"days": days},
        }

    days = []
    for i, kp in enumerate(focus, start=1):
        tasks = [f"复习知识点：{kp['name']}（基础练习 3 题）"]
        if i == len(focus):
            tasks.append("综合练习 5 题")
            tasks.append("错题回顾 2 题")
        else:
            tasks.append("巩固练习 3 题")
        days.append({"label": f"第{i}天", "tasks": tasks})
    days[0]["tasks"].insert(0, barrier_task)

    kp_names = "、".join(kp["name"] for kp in focus)
    return {
        "title": f"{student.name}的学习计划（{len(days)} 天）",
        "plan_text": (
            "## 学习计划\n\n"
            f"基于近期作答数据，{student.name} 在以下知识点掌握度待提升：**{kp_names}**。\n\n"
            f"当前主导障碍为「{BARRIER_LABELS[dominant]}」，建议每日完成对应任务，"
            f"第 {len(days)} 天进行综合复习与错题回顾。"
        ),
        "plan_data": {"days": days},
    }


def apply_plan(db: Session, student_id: int, plan: dict) -> dict:
    """落库学习计划 + 向有效绑定家长创建学习计划通知（单事务）。"""
    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError(detail="学生不存在", error_code="STUDENT_NOT_FOUND")
    student.learning_plan = plan
    title = f"{student.name}的学习计划已生成"
    rows = (
        db.query(StudentParentBinding.parent_id)
        .filter(
            StudentParentBinding.student_id == student_id,
            StudentParentBinding.status == "active",
        )
        .all()
    )
    for (parent_id,) in rows:
        db.add(
            ParentNotification(
                parent_id=parent_id,
                notification_type=NotificationType.learning_plan,
                title=title,
                content=plan.get("summary") or plan.get("title") or "学习计划",
            )
        )
    db.commit()
    return {
        "sent": True,
        "student_id": student_id,
        "name": student.name,
        "plan_title": title,
        "notified_parents": len(rows),
    }
