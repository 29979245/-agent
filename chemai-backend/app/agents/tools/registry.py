"""工具注册表：工具实现 + Guard 四层包装 + LangGraph 绑定（doc 30 §3 / §5）。

- TOOL_IMPLS：工具名 → 实现函数（签名 (ctx, **kwargs) -> dict）。
- TOOL_SCHEMAS：工具名 → pydantic 参数模型（供 LangChain @tool 生成 JSON Schema）。
- execute_tool：Guard 四层检查（D11 登记时机）→ 执行 → _component/_route 剥离并推送 SSE 指令。
- build_langgraph_tools：按 Persona 生成 LangGraph create_react_agent 可用工具列表。
"""
from __future__ import annotations

import inspect
import logging
import time
from typing import Any, Callable, Optional

from app.core.exceptions import ForbiddenError, NotFoundError
from app.agents.audit import audit_logger
from app.agents.guard import GuardState, strip_special_fields
from app.agents.tools.context import ToolContext
from app.agents.tools.tool_meta import TOOL_META, tools_for_persona

logger = logging.getLogger(__name__)

TOOL_IMPLS: dict[str, Callable[..., Any]] = {}
TOOL_SCHEMAS: dict[str, type] = {}


def register_impl(name: str, schema: type, fn: Callable[..., Any]) -> Callable[..., Any]:
    """注册工具实现与参数 Schema（模块加载时执行）。"""
    TOOL_IMPLS[name] = fn
    TOOL_SCHEMAS[name] = schema
    return fn


# ---------- 出题组（tools_exam） ----------
from app.agents.tools import tools_exam  # noqa: E402

register_impl("search_exam_bank", tools_exam.SearchExamBankArgs, tools_exam.search_exam_bank)
register_impl("web_search", tools_exam.WebSearchArgs, tools_exam.web_search)
register_impl("show_exam_workbench", tools_exam.ShowExamWorkbenchArgs, tools_exam.show_exam_workbench)
register_impl("generate_questions", tools_exam.GenerateQuestionsArgs, tools_exam.generate_questions)
register_impl("save_to_bank", tools_exam.SaveToBankArgs, tools_exam.save_to_bank)
register_impl("list_banks", tools_exam.ListBanksArgs, tools_exam.list_banks)
register_impl("delete_bank", tools_exam.DeleteBankArgs, tools_exam.delete_bank)

# ---------- 诊断组（tools_diagnosis） ----------
from app.agents.tools import tools_diagnosis  # noqa: E402

register_impl("diagnose_barrier", tools_diagnosis.DiagnoseBarrierArgs, tools_diagnosis.diagnose_barrier)
register_impl("show_diagnosis", tools_diagnosis.ShowDiagnosisArgs, tools_diagnosis.show_diagnosis)
register_impl("show_students", tools_diagnosis.ShowStudentsArgs, tools_diagnosis.show_students)
register_impl("weekly_report", tools_diagnosis.WeeklyReportArgs, tools_diagnosis.weekly_report)
register_impl("assign_adaptive_practice", tools_diagnosis.AssignAdaptivePracticeArgs, tools_diagnosis.assign_adaptive_practice)
register_impl("generate_learning_plan", tools_diagnosis.GenerateLearningPlanArgs, tools_diagnosis.generate_learning_plan)
register_impl("send_learning_plan", tools_diagnosis.SendLearningPlanArgs, tools_diagnosis.send_learning_plan)

# ---------- 辅导组（tools_tutoring） ----------
from app.agents.tools import tools_tutoring  # noqa: E402

register_impl("ionic_equation_tutor", tools_tutoring.TopicTutorArgs, tools_tutoring.ionic_equation_tutor)
register_impl("stoichiometry_tutor", tools_tutoring.TopicTutorArgs, tools_tutoring.stoichiometry_tutor)
register_impl("redox_tutor", tools_tutoring.TopicTutorArgs, tools_tutoring.redox_tutor)
register_impl("equilibrium_tutor", tools_tutoring.TopicTutorArgs, tools_tutoring.equilibrium_tutor)
register_impl("periodic_law_tutor", tools_tutoring.TopicTutorArgs, tools_tutoring.periodic_law_tutor)
register_impl("organic_tutor", tools_tutoring.TopicTutorArgs, tools_tutoring.organic_tutor)
register_impl("chemistry_tutor", tools_tutoring.ChemistryTutorArgs, tools_tutoring.chemistry_tutor)
register_impl("simulate_experiment", tools_tutoring.SimulateExperimentArgs, tools_tutoring.simulate_experiment)
register_impl("balance_equation", tools_tutoring.BalanceEquationArgs, tools_tutoring.balance_equation)

# ---------- OCR 批改组（tools_ocr） ----------
from app.agents.tools import tools_ocr  # noqa: E402

register_impl("query_ocr_progress", tools_ocr.QueryOcrProgressArgs, tools_ocr.query_ocr_progress)
register_impl("grade_answer_sheets", tools_ocr.GradeAnswerSheetsArgs, tools_ocr.grade_answer_sheets)
register_impl("save_grading_results", tools_ocr.SaveGradingResultsArgs, tools_ocr.save_grading_results)

# ---------- 记忆组（tools_memory） ----------
from app.agents.tools import tools_memory  # noqa: E402

register_impl("memory_student_get", tools_memory.MemoryStudentGetArgs, tools_memory.memory_student_get)
register_impl("memory_teacher_get", tools_memory.MemoryTeacherGetArgs, tools_memory.memory_teacher_get)

# ---------- 家长报告组（tools_parent_report） ----------
from app.agents.tools import tools_parent_report  # noqa: E402

register_impl("generate_parent_report", tools_parent_report.GenerateParentReportArgs, tools_parent_report.generate_parent_report)
register_impl("send_report_to_parent", tools_parent_report.SendReportToParentArgs, tools_parent_report.send_report_to_parent)

# ---------- 浏览器组（tools_browser） ----------
from app.agents.tools import tools_browser  # noqa: E402

register_impl("browse_navigate", tools_browser.BrowseNavigateArgs, tools_browser.browse_navigate)
register_impl("browse_read", tools_browser.BrowseReadArgs, tools_browser.browse_read)
register_impl("browse_click", tools_browser.BrowseClickArgs, tools_browser.browse_click)
register_impl("browse_input", tools_browser.BrowseInputArgs, tools_browser.browse_input)
register_impl("browse_screenshot", tools_browser.BrowseScreenshotArgs, tools_browser.browse_screenshot)


# ---------------------------------------------------------------- 执行包装

def _emit_directives(ctx: ToolContext, directives: dict, clean: dict) -> None:
    """把剥离的 _component/_route 指令推送为 SSE component/navigate 事件（doc 41 §3.5/3.6）。

    _route 支持三段式协议 {navigate, populate, actions}（doc 25 §5.4）与旧 {page, params} 形式，
    分别发 navigate / populate / action 事件。
    """
    if ctx is None or ctx.emit is None or not directives:
        return
    if "component" in directives:
        ctx.emit("component", {"component": directives["component"], "params": clean})
    if "route" in directives:
        route = directives["route"]
        if isinstance(route, str):
            ctx.emit("navigate", {"page": route, "params": {}})
        elif isinstance(route, dict):
            nav = route.get("navigate")
            if isinstance(nav, dict):
                ctx.emit("navigate", {"page": nav.get("page"), "params": nav.get("params", {})})
            elif nav is not None:
                ctx.emit("navigate", {"page": nav, "params": route.get("params", {})})
            elif "page" in route:
                ctx.emit("navigate", {"page": route.get("page"), "params": route.get("params", {})})
            if "populate" in route:
                ctx.emit("populate", route["populate"])
            for action in route.get("actions") or []:
                ctx.emit("action", action)


async def execute_tool(ctx: ToolContext, name: str, kwargs: dict) -> dict:
    """Guard 四层 → 执行 → 审计 → 特殊字段剥离 → 指令推送（doc 30 §5/§10 / D11）。"""
    guard: GuardState = ctx.safe_guard if ctx is not None else GuardState()
    check = guard.check(name, kwargs, ctx)
    if not check.ok:
        error = dict(check.error)
        if check.payload:
            error.update(check.payload)  # approval 阻塞携带 requires_approval/tool/args（D13）
        return error
    # D11：审批通过后、执行开始时登记执行键（审批阻塞未启动→不登记）
    guard.register_execution(name, kwargs)
    guard.record_call(name)
    impl = TOOL_IMPLS[name]  # 先取实现（KeyError 在并发计数前，不泄漏在途槽）
    guard.begin_execution(name)  # L2 并发在途 +1（finally 归还）
    persona = getattr(ctx, "persona", "") if ctx is not None else ""
    started = time.monotonic()
    try:
        result = impl(ctx, **kwargs)
        if inspect.isawaitable(result):
            result = await result
    except ForbiddenError as exc:
        return _audit_and_emit(ctx, persona, name, kwargs, started,
                               error={"error": "forbidden", "message": str(exc.detail or exc), "_guard_error": True})
    except NotFoundError as exc:
        return _audit_and_emit(ctx, persona, name, kwargs, started,
                               error={"error": "not_found", "message": str(exc.detail or exc), "_guard_error": True})
    except Exception as exc:  # noqa: BLE001 —— 工具失败统一转换为可读错误
        logger.warning("[tool] %s 执行失败：%s", name, exc)
        return _audit_and_emit(ctx, persona, name, kwargs, started,
                               error={"error": "tool_failed", "message": str(exc), "_guard_error": True})
    finally:
        guard.end_execution(name)  # L2 并发在途 -1（异常路径同样归还）
    return _audit_and_emit(ctx, persona, name, kwargs, started, result=result)


def _audit_and_emit(
    ctx: ToolContext, persona: str, name: str, kwargs: dict, started: float, *, result=None, error=None
) -> dict:
    """统一收口：记录审计 → 剥离特殊字段 → 推送 SSE 指令 → 返回纯净结果/错误。"""
    duration_ms = (time.monotonic() - started) * 1000.0
    if error is not None:
        audit_logger.log(persona=persona, skill_name=name, args=kwargs,
                         result_summary=str(error.get("message") or ""),
                         duration_ms=duration_ms, error=str(error.get("message") or ""))
        return error
    if result is None:
        audit_logger.log(persona=persona, skill_name=name, args=kwargs,
                         result_summary="ok", duration_ms=duration_ms)
        return {"ok": True}
    if isinstance(result, dict):
        clean, directives = strip_special_fields(dict(result))
        audit_logger.log(persona=persona, skill_name=name, args=kwargs,
                         result_summary=clean, duration_ms=duration_ms)
        _emit_directives(ctx, directives, clean)
        return clean
    audit_logger.log(persona=persona, skill_name=name, args=kwargs,
                     result_summary=result, duration_ms=duration_ms)
    return result


def build_langgraph_tools(ctx: ToolContext, persona: str, skills: Optional[list[str]] = None):
    """按 Persona 生成 LangGraph create_react_agent 工具列表（doc 30 §4.2 工具集）。

    skills 为「Persona YAML 白名单 ∩ TOOL_META」交集（spec Persona 工具过滤）；
    缺省回退 TOOL_META 注册的该 Persona 全部工具。
    """
    from langchain_core.tools import tool

    names = skills if skills is not None else tools_for_persona(persona)
    bound = []
    for name in names:
        meta = TOOL_META.get(name)
        schema = TOOL_SCHEMAS.get(name)
        if meta is None or schema is None:
            logger.warning("[tools] Persona %s 含未注册实现: %s", persona, name)
            continue

        @tool(name, description=meta.description, args_schema=schema)
        async def _wrapped(_name: str = name, **kwargs: Any) -> dict:
            return await execute_tool(ctx, _name, kwargs)

        bound.append(_wrapped)
    return bound
