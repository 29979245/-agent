"""诊断与学生工具组（doc 30 §3.3，7 工具）——障碍诊断/诊断面板/学生列表/周报/自适应练习/学习计划。

契约要点：
- 智能名称解析（doc 30 §3.3）：纯数字走 ID，中文姓名模糊匹配，多结果返回候选列表。
- 家长隐私：parent 角色仅可访问已绑定子女（data_access 约束），越权返回 403 语义错误。
- weekly_report 复用既有生成管线，通过 CompleteAdapter 把 Agent LLMClient 适配为 .complete(messages)。
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models import Account, Class, Student, StudentParentBinding
from app.agents.tools import tools_memory
from app.services.analytics.panel_service import class_students
from app.services.analytics.weekly_report_service import get_or_generate_weekly_report
from app.services.diagnosis.aggregation import BARRIER_LABELS, dominant_axis, normalize_profile
from app.services.diagnosis.learning_plan import apply_plan
from app.services.exercise.adaptive import AdaptivePracticeService
from app.agents.tools.context import ToolContext, user_id as _user_id, user_role as _user_role
from app.agents.tools.llm_adapter import CompleteAdapter

BARRIER_AXES = ("concept", "reading", "expression")

# ---------------------------------------------------------------- 参数 Schema

class StudentRefArgs(BaseModel):
    student_id: Optional[int] = Field(default=None, description="学生 ID")
    student_name: Optional[str] = Field(default=None, description="学生姓名")
    class_id: Optional[int] = Field(default=None, description="班级 ID")
    class_name: Optional[str] = Field(default=None, description="班级名")


class DiagnoseBarrierArgs(StudentRefArgs):
    pass


class ShowDiagnosisArgs(StudentRefArgs):
    pass


class ShowStudentsArgs(BaseModel):
    class_id: Optional[int] = Field(default=None, description="班级 ID")
    class_name: Optional[str] = Field(default=None, description="班级名")
    barrier_filter: Optional[str] = Field(default=None, description="障碍过滤：concept/reading/expression")


class WeeklyReportArgs(BaseModel):
    student_id: Optional[int] = Field(default=None)
    student_name: Optional[str] = Field(default=None)
    class_name: Optional[str] = Field(default=None)
    class_id: Optional[int] = Field(default=None)


class AssignAdaptivePracticeArgs(BaseModel):
    class_id: Optional[int] = Field(default=None, description="班级 ID")
    class_name: Optional[str] = Field(default=None, description="班级名")
    count: int = Field(default=3, ge=1, le=10, description="每生题目数")
    knowledge_points: Optional[str] = Field(default=None, description="知识点约束（可选）")


class GenerateLearningPlanArgs(BaseModel):
    student_id: Optional[int] = Field(default=None)
    student_name: Optional[str] = Field(default=None)


class SendLearningPlanArgs(BaseModel):
    student_id: int = Field(..., description="学生 ID")
    plan_data: dict = Field(default_factory=dict, description="学习计划内容")


# ---------------------------------------------------------------- 解析辅助

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 固定年级词（高一/高二/高三/初一…），其中的中文数字是词本身的一部分，
# 不得当作班级序号转换——否则「高一（3）班」会被误规范成「高13班」。
_GRADE_PREFIXES = (
    "一年级", "二年级", "三年级", "四年级", "五年级", "六年级",
    "七年级", "八年级", "九年级",
    "初一", "初二", "初三", "高一", "高二", "高三",
)


def _normalize_class_name(name: str) -> str:
    """班级名数字规范化：'高一（一）班' → '高一1班'（doc 30 §3.3）。

    仅转换班级序号里的中文数字；固定年级词整体保留。
    """
    name = re.sub(r"[（(（\s)]", "", name)
    name = re.sub(r"[）)]", "", name)
    protected: dict[str, str] = {}
    for i, prefix in enumerate(_GRADE_PREFIXES):
        token = f"\x00{i}\x00"
        if prefix in name:
            name = name.replace(prefix, token)
            protected[token] = prefix
    out: list[str] = []
    for ch in name:
        out.append(str(_CN_DIGITS[ch]) if ch in _CN_DIGITS else ch)
    result = "".join(out)
    for token, prefix in protected.items():
        result = result.replace(token, prefix)
    return result


def _bound_student_ids(ctx: ToolContext) -> set[int]:
    """家长角色可访问的子女 id 集合（StudentParentBinding active）。

    身份链：user_id = Account.id → Account.role_id = Parent.id → 绑定表。
    """
    if ctx.db is None or _user_role(ctx) != "parent":
        return set()
    account = ctx.db.get(Account, _user_id(ctx))
    if account is None:
        return set()
    rows = ctx.db.query(StudentParentBinding.student_id).filter(
        StudentParentBinding.parent_id == account.role_id,
        StudentParentBinding.status == "active",
    ).all()
    return {row[0] for row in rows}


def _assert_parent_ok(ctx: ToolContext, student_id: int) -> None:
    """家长越权访问其他学生 → ForbiddenError（doc 30 §4.2 隐私）。"""
    if _user_role(ctx) != "parent":
        return
    if student_id not in _bound_student_ids(ctx):
        raise ForbiddenError(detail="家长仅可查看自己孩子的学情")


def _resolve_student(ctx: ToolContext, student_id=None, student_name=None) -> tuple[Student | None, list[dict]]:
    """智能名称解析：ID 精确；姓名模糊匹配；多结果返回候选。"""
    if ctx.db is None:
        return None, []
    if student_id:
        student = ctx.db.get(Student, student_id)
        if student is None:
            return None, []
        _assert_parent_ok(ctx, student_id)
        return student, []
    name = (student_name or "").strip()
    if not name:
        return None, []
    rows = ctx.db.query(Student).filter(Student.name == name).all()
    if not rows:
        rows = ctx.db.query(Student).filter(Student.name.like(f"%{name}%")).all()
    if len(rows) == 1:
        _assert_parent_ok(ctx, rows[0].id)
        return rows[0], []
    cands = [{"student_id": s.id, "name": s.name} for s in rows]
    return None, cands


def _resolve_class(ctx: ToolContext, class_id=None, class_name=None) -> tuple[Class | None, list[dict]]:
    """班级解析：ID 精确；名称数字规范化匹配；多结果返回候选。"""
    if ctx.db is None:
        return None, []
    if class_id:
        cls = ctx.db.get(Class, class_id)
        return (cls, []) if cls is not None else (None, [])
    name = _normalize_class_name(class_name or "")
    if not name:
        return None, []
    exact = ctx.db.query(Class).filter(Class.name == class_name).all()
    if not exact:
        # 两侧同规范化后比较：免疫全/半角括号、空白、中文数字写法差异
        # （LLM 常把「高一（3）班」规整成「高一(3)班 / 高一3班 / 高一三班」）。
        all_classes = ctx.db.query(Class).all()
        exact = [c for c in all_classes if _normalize_class_name(c.name) == name]
        if not exact:
            exact = [c for c in all_classes if name in _normalize_class_name(c.name)]
    if len(exact) == 1:
        return exact[0], []
    cands = [{"class_id": c.id, "name": c.name} for c in exact]
    return None, cands


def _profile_payload(student: Student) -> dict:
    profile = normalize_profile(student.barrier_profile)
    return {
        "profile": profile,
        "dominant_barrier": dominant_axis(profile),
        "dominant_label": BARRIER_LABELS[dominant_axis(profile)],
    }


# ---------------------------------------------------------------- 工具实现

async def diagnose_barrier(
    ctx: ToolContext,
    student_id: Optional[int] = None,
    student_name: Optional[str] = None,
    class_id: Optional[int] = None,
    class_name: Optional[str] = None,
) -> dict:
    """个体或班级两级障碍诊断（doc 30 §3.3）。"""
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}

    # 家长隐私（doc 30 §4.2）：家长仅支持个体诊断，携带班级参数即越权意图（含 student+class 双参数）
    if _user_role(ctx) == "parent" and (class_id is not None or class_name):
        raise ForbiddenError(detail="家长仅支持个体诊断")

    student, cands = _resolve_student(ctx, student_id, student_name)
    if cands:
        return {"candidates": cands, "message": "匹配到多位学生，请指定具体学生", "total_candidates": len(cands)}
    if student is not None:
        # D3 写接线①：个体诊断完成 → 画像快照写长期记忆（best-effort，供 memory_student_get 读回）
        tools_memory.push_student_diagnosis_memory(ctx, student.id, source="diagnose_barrier")
        return {
            "student_id": student.id,
            "name": student.name,
            **_profile_payload(student),
            "_component": "diagnosis-chart",
        }

    cls, cands = _resolve_class(ctx, class_id, class_name)
    if cands:
        return {"candidates": cands, "message": "匹配到多个班级，请指定具体班级", "total_candidates": len(cands)}
    if cls is not None:
        students = ctx.db.query(Student).filter(Student.class_id == cls.id).all()
        profiles = [normalize_profile(s.barrier_profile) for s in students]
        distribution = {axis: round(sum(p[axis] for p in profiles) / len(profiles), 2) if profiles else 0.0 for axis in BARRIER_AXES}
        return {
            "class_id": cls.id,
            "class_name": cls.name,
            "total_students": len(students),
            "distribution": distribution,
            "students": [{"student_id": s.id, "name": s.name, **_profile_payload(s)} for s in students],
            "_component": "diagnosis-chart",
        }
    return {"error": "not_found", "message": "未找到该学生/班级", "_guard_error": True}


async def show_diagnosis(
    ctx: ToolContext,
    student_id: Optional[int] = None,
    student_name: Optional[str] = None,
    class_id: Optional[int] = None,
    class_name: Optional[str] = None,
) -> dict:
    """内联渲染诊断图表面板（doc 30 §3.3）。"""
    result = await diagnose_barrier(ctx, student_id, student_name, class_id, class_name)
    if "_component" not in result:
        return result
    result["_component"] = "diagnosis-panel"
    return result


async def show_students(
    ctx: ToolContext,
    class_id: Optional[int] = None,
    class_name: Optional[str] = None,
    barrier_filter: Optional[str] = None,
) -> dict:
    """三模式学生列表（doc 30 §3.3）：无班级→班级；有班级→学生卡片；有过滤→按障碍筛选。"""
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    if not class_id and not class_name:
        classes = ctx.db.query(Class).order_by(Class.id).all()
        return {
            "mode": "classes",
            "items": [{"class_id": c.id, "name": c.name, "student_count": c.student_count} for c in classes],
            "_component": "student-list",
        }
    cls, cands = _resolve_class(ctx, class_id, class_name)
    if cands:
        return {"mode": "class_candidates", "candidates": cands, "message": "匹配到多个班级"}
    if cls is None:
        return {"error": "not_found", "message": "未找到该班级", "_guard_error": True}
    data = class_students(ctx.db, cls.id)
    items = []
    for s in data["items"]:
        dom = dominant_axis(s["barrier_profile"])
        if barrier_filter and dom != barrier_filter:
            continue
        items.append({
            "student_id": s["id"],
            "name": s["name"],
            "dominant_barrier": dom,
            "dominant_label": BARRIER_LABELS[dom],
            "profile": s["barrier_profile"],
            "score_trend": s.get("score_trend") or [],
            "is_active": s.get("is_active", False),
        })
    return {
        "mode": "students",
        "class_id": cls.id,
        "class_name": cls.name,
        "barrier_filter": barrier_filter or "",
        "total": len(items),
        "items": items,
        "_component": "student-list",
    }


def _render_report_text(report: dict) -> str:
    """把 {summary, detail, advice} 拼成自然语言段落。

    周报本身已是 LLM 生成的家长向文本，工具只应把渲染好的段落交给 Agent LLM——
    若把原始 dict 作为工具结果返回，Agent LLM 可能把 JSON 原样复读进回复
    （实证：家长端出现 `{"summary":...}本周完成4次练习...` 的拼接输出）。
    """
    if report.get("no_data"):
        return (report.get("summary") or "本周暂无练习记录").strip()
    parts = []
    for key in ("summary", "detail", "advice"):
        text = (report.get(key) or "").strip()
        if text:
            parts.append(text if text.endswith(("。", "！", "？")) else text + "。")
    return "".join(parts)


async def weekly_report(
    ctx: ToolContext,
    student_id: Optional[int] = None,
    student_name: Optional[str] = None,
    class_name: Optional[str] = None,
    class_id: Optional[int] = None,
) -> dict:
    """LLM 生成 ≤200 字自然语言周报（doc 30 §3.3，通俗不制造焦虑）。

    返回 report_text（渲染好的纯文本段落）而非原始 {summary,detail,advice} dict，
    避免 Agent LLM 把工具 JSON 复读进家长对话。
    """
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    student, cands = _resolve_student(ctx, student_id, student_name)
    if cands:
        return {"candidates": cands, "message": "匹配到多位学生，请指定具体学生"}
    if student is None:
        return {"error": "not_found", "message": "未找到该学生，请提供学生 ID 或姓名", "_guard_error": True}
    if ctx.llm is None:
        return {"error": "llm_unavailable", "message": "LLM 未配置，无法生成周报", "_guard_error": True}
    client = CompleteAdapter(ctx.llm)
    report = get_or_generate_weekly_report(ctx.db, student.id, client=client)
    return {
        "student_id": student.id,
        "name": student.name,
        "report_text": _render_report_text(report),
        "no_data": bool(report.get("no_data")),
    }


async def assign_adaptive_practice(
    ctx: ToolContext,
    class_id: Optional[int] = None,
    class_name: Optional[str] = None,
    count: int = 3,
    knowledge_points: Optional[str] = None,
) -> dict:
    """为班级学生生成个性化 ZPD 练习预览（doc 28 §六，preview-only 不落库）。

    预览无副作用，不创建练习记录、不复制题目入库；教师确认后由前端调用
    POST /api/practice/adaptive/confirm 持久化（design D1/D2）。
    """
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    cls, cands = _resolve_class(ctx, class_id, class_name)
    if cands:
        return {"candidates": cands, "message": "匹配到多个班级，请指定具体班级"}
    if cls is None:
        return {"error": "not_found", "message": "未找到该班级", "_guard_error": True}
    student_ids = [s.id for s in ctx.db.query(Student).filter(Student.class_id == cls.id).all()]
    if not student_ids:
        return {"class_id": cls.id, "class_name": cls.name, "results": [], "batch_limit": 5, "remaining": 0}
    data = AdaptivePracticeService(ctx.db).preview_batch(student_ids, count=count)
    return {
        "class_id": cls.id,
        "class_name": cls.name,
        "batch_limit": data["batch_limit"],
        "remaining": data["remaining"],
        "results": data["results"],
    }


async def generate_learning_plan(
    ctx: ToolContext,
    student_id: Optional[int] = None,
    student_name: Optional[str] = None,
) -> dict:
    """跳转学生管理页并触发学习方案生成（doc 30 §3.3）。"""
    student, cands = _resolve_student(ctx, student_id, student_name)
    if cands:
        return {"candidates": cands, "message": "匹配到多位学生，请指定具体学生"}
    if student is None:
        return {"error": "not_found", "message": "未找到该学生", "_guard_error": True}
    return {
        "student_id": student.id,
        "name": student.name,
        "_route": {"page": "students", "params": {"student_id": student.id, "action": "learning-plan"}},
    }


async def send_learning_plan(ctx: ToolContext, student_id: int, plan_data: dict) -> dict:
    """持久化学习计划并通知家长（doc 30 §3.3，复用 apply_plan 防行为漂移）。"""
    if ctx.db is None:
        return {"error": "db_unavailable", "message": "数据库未注入", "_guard_error": True}
    try:
        return apply_plan(ctx.db, student_id, plan_data)
    except NotFoundError:
        return {"error": "not_found", "message": "未找到该学生", "_guard_error": True}
