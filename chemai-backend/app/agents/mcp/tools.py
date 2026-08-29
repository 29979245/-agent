"""16 个 MCP 工具实现（doc 30 十二 / spec「MCP 工具服务器」）。

每个工具 = mcp_tool 装饰器注册的 async handler(db, user, **args)；
统一复用 services 层既有实现，写操作在 handler 内 commit（get_db 不自动提交）。
角色门控：trigger_warning_check / get_pending_warnings 仅教师；send_notification
教师/家长；其余任意已认证角色。
"""
from __future__ import annotations

import dataclasses
import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.mcp.registry import mcp_tool
from app.db.models import (
    ExamRecord,
    ExamStatus,
    ExamType,
    ParentNotification,
    Question,
    ReviewTask,
    Student,
    StudentAnswer,
    WarningLog,
)
from app.db.models.enums import NotificationType, WarningStatus
from app.services.analytics.aggregation import knowledge_point_error_rates
from app.services.analytics.early_warning import EarlyWarningService
from app.services.analytics.panel_service import load_class_panel, load_student_detail
from app.services.exercise.spaced_repetition import SpacedRepetitionEngine
from app.services.exercise.wrong_question import WrongQuestionTrainer


def _commit(db) -> None:
    db.commit()


def _review_task_dict(t: ReviewTask) -> dict:
    return {
        "id": t.id,
        "student_id": t.student_id,
        "question_id": t.question_id,
        "review_level": t.review_level.value,
        "status": t.status.value,
        "next_review_at": t.next_review_at.isoformat() if t.next_review_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "consecutive_correct": t.consecutive_correct,
        "consecutive_error": t.consecutive_error,
    }


# ---------------------------------------------------------------- 参数 Schema


class OcrRecognizeArgs(BaseModel):
    path: str = Field(..., description="待识别文件路径（本地绝对路径）")


class GenerateQuestionsArgs(BaseModel):
    knowledge_points: str = Field(..., description="知识点，逗号分隔")
    difficulty: Literal["easy", "medium", "hard", "competition"] = Field("medium", description="难度")
    count: int = Field(default=3, ge=1, le=10, description="生成数量")
    question_type: str | None = Field(default=None, description="题型（选择题/填空题等）")


class GenerateVariantArgs(BaseModel):
    student_id: int = Field(..., description="学生 ID")
    question_id: int = Field(..., description="原题 ID")
    count: int = Field(default=1, ge=1, le=10, description="变式数量")


class GetWrongQuestionsArgs(BaseModel):
    student_id: int = Field(..., description="学生 ID")


class CreateTrainingArgs(BaseModel):
    student_id: int = Field(..., description="学生 ID")
    question_ids: list[int] = Field(..., min_length=1, description="训练题目 ID 列表")


class SubmitTrainingArgs(BaseModel):
    student_id: int = Field(..., description="学生 ID")
    answers: list[dict] = Field(..., min_length=1, description="作答列表 [{question_id, selected_option}]")


class GetReviewTasksArgs(BaseModel):
    student_id: int = Field(..., description="学生 ID")


class CompleteReviewArgs(BaseModel):
    task_id: int = Field(..., description="复习任务 ID")
    passed: bool = Field(..., description="是否通过（提升/降级等级）")


class GetClassOverviewArgs(BaseModel):
    class_id: int = Field(..., description="班级 ID")


class GetStudentStatsArgs(BaseModel):
    class_id: int = Field(..., description="班级 ID")
    student_id: int = Field(..., description="学生 ID")


class TriggerWarningCheckArgs(BaseModel):
    pass


class GetPendingWarningsArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=100, description="返回数量上限")


class SendNotificationArgs(BaseModel):
    parent_id: int = Field(..., description="家长 ID")
    notification_type: Literal[
        "weekly_report", "score_alert", "learning_plan", "reminder", "daily_report"
    ] = Field(..., description="通知类型")
    title: str = Field(..., description="通知标题")
    content: str = Field(default="", description="通知内容")


class DiagnoseQuestionArgs(BaseModel):
    inputs: dict = Field(..., description="诊断输入 {question, answer, correct_answer, history, ...}")


class GetBarrierDistributionArgs(BaseModel):
    class_id: int = Field(..., description="班级 ID")
    exam_id: int = Field(..., description="考试 ID")


class GetKnowledgeHeatmapArgs(BaseModel):
    class_id: int = Field(..., description="班级 ID")


# ---------------------------------------------------------------- OCR


@mcp_tool("ocr_recognize", "识别图片/PDF 作业为结构化答案", OcrRecognizeArgs)
async def _ocr_recognize(db, user, *, path: str) -> dict:
    from app.services.ocr.engines.base import OCRDocument
    from app.services.ocr.engines.router import extract_document

    result = await extract_document(OCRDocument(path=path))
    return dataclasses.asdict(result)


# ---------------------------------------------------------------- 出题


@mcp_tool("generate_questions", "AI 出题：RAG 检索 → LLM 生成 → 化学式标准化 → 四维审核", GenerateQuestionsArgs)
async def _generate_questions(db, user, *, knowledge_points: str, difficulty: str, count: int, question_type: str | None) -> dict:
    from app.agents.factories.model_factory import LLMClient
    from app.agents.tools.chem_formula import normalize_chem_formulas
    from app.agents.tools.tools_exam import (
        _GENERATE_SYSTEM_PROMPT,
        _audit_question,
        _coerce_difficulty,
        _extract_json_array,
    )
    from app.services.question.serializers import split_knowledge_points

    difficulty = _coerce_difficulty(difficulty)
    parts = [f"知识点：{knowledge_points}", f"难度：{difficulty}", f"数量：{count}"]
    if question_type:
        parts.append(f"题型：{question_type}")
    messages = [
        {"role": "system", "content": _GENERATE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]
    try:
        raw = await LLMClient().acomplete_chain(messages)
        questions = _extract_json_array(raw)[:count]
    except Exception as exc:  # noqa: BLE001 —— LLM 类工具失败返回可读错误
        return {"error": "llm_generation_failed", "message": f"生成失败：{exc}"}

    for q in questions:
        q["knowledge_points"] = q.get("knowledge_points") or split_knowledge_points(knowledge_points)
        q["difficulty"] = _coerce_difficulty(str(q.get("difficulty") or difficulty))
        q["content"] = normalize_chem_formulas(q.get("content", "") or "")
        q["options"] = [normalize_chem_formulas(str(o)) for o in (q.get("options") or [])]
        q["analysis"] = normalize_chem_formulas(q.get("analysis", "") or "")
        q["answer"] = normalize_chem_formulas(q.get("answer", "") or "")
        q["audit"] = _audit_question(q)

    return {
        "count": len(questions),
        "difficulty": difficulty,
        "knowledge_points": split_knowledge_points(knowledge_points),
        "questions": questions,
    }


# ---------------------------------------------------------------- 错题强化


@mcp_tool("generate_variant", "同知识点同难度生成变式题（排除原题）", GenerateVariantArgs)
async def _generate_variant(db, user, *, student_id: int, question_id: int, count: int) -> dict:
    result = WrongQuestionTrainer(db).generate_variants(student_id, question_id, count)
    _commit(db)
    return result


@mcp_tool("get_wrong_questions", "列出学生错题（按答错次数降序）", GetWrongQuestionsArgs)
async def _get_wrong_questions(db, user, *, student_id: int) -> dict:
    items = WrongQuestionTrainer(db).list_wrong_questions(student_id)
    return {"student_id": student_id, "count": len(items), "items": items}


@mcp_tool("create_training", "创建错题训练会话（生成训练考试记录）", CreateTrainingArgs)
async def _create_training(db, user, *, student_id: int, question_ids: list[int]) -> dict:
    existing = {q.id for q in db.query(Question).filter(Question.id.in_(question_ids)).all()}
    missing = [qid for qid in question_ids if qid not in existing]
    if missing:
        return {"error": "question_not_found", "message": f"题目不存在：{missing}"}
    exam = ExamRecord(
        class_id=None,
        student_id=student_id,
        name="错题训练",
        exam_type=ExamType.practice,
        status=ExamStatus.published,
        exam_date=datetime.date.today(),
        question_stats={"mode": "training", "question_ids": list(question_ids), "deadline": None},
    )
    db.add(exam)
    _commit(db)
    return {"student_id": student_id, "exam_id": exam.id, "question_count": len(question_ids)}


@mcp_tool("submit_training", "提交训练作答：逐题批改并同步答错复习任务", SubmitTrainingArgs)
async def _submit_training(db, user, *, student_id: int, answers: list[dict]) -> dict:
    result = WrongQuestionTrainer(db).start_training(student_id, answers)
    _commit(db)
    return result


# ---------------------------------------------------------------- 间隔复习


@mcp_tool("get_review_tasks", "列出学生到期复习任务", GetReviewTasksArgs)
async def _get_review_tasks(db, user, *, student_id: int) -> dict:
    tasks = SpacedRepetitionEngine(db).list_due_tasks(student_id)
    return {"student_id": student_id, "count": len(tasks), "items": [_review_task_dict(t) for t in tasks]}


@mcp_tool("complete_review", "完成一次复习（通过提升等级 / 未通过降级）", CompleteReviewArgs)
async def _complete_review(db, user, *, task_id: int, passed: bool) -> dict:
    task = db.get(ReviewTask, task_id)
    if task is None:
        return {"error": "task_not_found", "message": f"复习任务不存在：{task_id}"}
    updated = SpacedRepetitionEngine(db).apply_review(task, passed)
    _commit(db)
    return _review_task_dict(updated)


# ---------------------------------------------------------------- 学情分析


@mcp_tool("get_class_overview", "班级学情面板完整数据（均分趋势/知识点掌握/障碍分布）", GetClassOverviewArgs, requires=("teacher",))
async def _get_class_overview(db, user, *, class_id: int) -> dict:
    return load_class_panel(db, class_id)


@mcp_tool("get_student_stats", "单学生学情明细（正确率/知识点/障碍画像）", GetStudentStatsArgs, requires=("teacher",))
async def _get_student_stats(db, user, *, class_id: int, student_id: int) -> dict:
    return load_student_detail(db, class_id, student_id)


@mcp_tool("get_barrier_distribution", "指定考试三维障碍分布（无数据回退历史画像）", GetBarrierDistributionArgs, requires=("teacher",))
async def _get_barrier_distribution(db, user, *, class_id: int, exam_id: int) -> dict:
    from sqlalchemy import func

    rows = (
        db.query(StudentAnswer.barrier_type, func.count(StudentAnswer.id))
        .join(ExamRecord, StudentAnswer.exam_id == ExamRecord.id)
        .filter(
            ExamRecord.id == exam_id,
            ExamRecord.class_id == class_id,
            StudentAnswer.barrier_type.isnot(None),
        )
        .group_by(StudentAnswer.barrier_type)
        .all()
    )
    total = sum(count for _, count in rows)
    if total > 0:
        dist = {bt.value: {"count": count, "pct": round(count / total, 2)} for bt, count in rows}
        return {"class_id": class_id, "exam_id": exam_id, "distribution": dist, "source": "current", "denominator": total}
    from app.services.diagnosis.aggregation import BARRIER_AXES, normalize_profile

    students = db.query(Student).filter(Student.class_id == class_id).all()
    counts = {axis: 0 for axis in BARRIER_AXES}
    profiled = 0
    for s in students:
        profile = normalize_profile(s.barrier_profile)
        dominant = max(profile, key=profile.get)
        if profile[dominant] > 0:
            counts[dominant] += 1
            profiled += 1
    dist = {
        axis: {"count": counts[axis], "pct": round(counts[axis] / profiled, 2) if profiled else 0.0}
        for axis in BARRIER_AXES
    }
    return {"class_id": class_id, "exam_id": exam_id, "distribution": dist, "source": "historical", "denominator": profiled}


@mcp_tool("get_knowledge_heatmap", "班级知识点错误率热力图（一题多知识点分别计）", GetKnowledgeHeatmapArgs, requires=("teacher",))
async def _get_knowledge_heatmap(db, user, *, class_id: int) -> dict:
    from app.services.analytics.panel_service import _question_kp_map

    student_ids = [s.id for s in db.query(Student).filter(Student.class_id == class_id).all()]
    answers = (
        db.query(StudentAnswer)
        .filter(StudentAnswer.student_id.in_(student_ids))
        .all()
    ) if student_ids else []
    rows = [(a.question_id, a.is_correct) for a in answers]
    kp_map = _question_kp_map(db, {a.question_id for a in answers})
    items = knowledge_point_error_rates(rows, kp_map)
    return {"class_id": class_id, "count": len(items), "items": items}


# ---------------------------------------------------------------- 预警


@mcp_tool("trigger_warning_check", "触发全量预警检测（未登录/成绩下滑/错题率过高）", TriggerWarningCheckArgs, requires=("teacher",))
async def _trigger_warning_check(db, user, **_: dict) -> dict:
    return EarlyWarningService(db).check_all_warnings()


@mcp_tool("get_pending_warnings", "列出待处理预警", GetPendingWarningsArgs, requires=("teacher",))
async def _get_pending_warnings(db, user, *, limit: int) -> dict:
    rows = (
        db.query(WarningLog)
        .filter(WarningLog.status == WarningStatus.pending)
        .order_by(WarningLog.created_at.desc())
        .limit(limit)
        .all()
    )
    items = [
        {
            "id": w.id,
            "student_id": w.student_id,
            "warning_type": w.warning_type.value,
            "level": w.level.value,
            "title": w.title,
            "content": w.content,
            "data": w.data,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in rows
    ]
    return {"count": len(items), "items": items}


@mcp_tool("send_notification", "向家长推送一条通知", SendNotificationArgs, requires=("teacher", "parent"))
async def _send_notification(db, user, *, parent_id: int, notification_type: str, title: str, content: str) -> dict:
    note = ParentNotification(
        parent_id=parent_id,
        notification_type=NotificationType(notification_type),
        title=title,
        content=content,
    )
    db.add(note)
    _commit(db)
    return {"sent": True, "notification_id": note.id, "parent_id": parent_id, "title": title}


# ---------------------------------------------------------------- 诊断


@mcp_tool("diagnose_question", "LLM 深度诊断学生作答障碍（concept/reading/expression）", DiagnoseQuestionArgs)
async def _diagnose_question(db, user, *, inputs: dict) -> dict:
    from app.services.diagnosis.llm_diagnosis import diagnose_llm

    return diagnose_llm(inputs).to_dict()
