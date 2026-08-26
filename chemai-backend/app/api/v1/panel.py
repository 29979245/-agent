"""学情面板 API（doc 31 §3，design.md D5/D7，挂载于 /api/panel）。

- GET /class/{class_id}                      班级学情面板完整数据
- GET /class/{class_id}/knowledge/{kp}       按知识点错误率分布与出错学生
- GET /class/{class_id}/student/{student_id} 学生学情详情
- GET /class/{class_id}/trend                 班级成绩趋势
- GET /export/{class_id}                      班级学情报告 PDF 导出
- GET /dashboard/{teacher_id}                 教师首页概览

权限（design.md D5）：analysis/read + 显式拒绝 student（矩阵中 student 有 analysis/read，
需 _ensure_not_student 二次拦截）；班级范围走 school_id 组织链隔离。
"""
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import require_permission
from app.db.models import Account, Class, Grade, Teacher
from app.db.session import get_db
from app.services.analytics.panel_service import (
    class_students,
    load_class_panel,
    load_dashboard,
    load_knowledge_detail,
    load_student_detail,
    load_trend,
    panel_report_html,
)
from app.services.question.export import pdf_bytes

panel_router = APIRouter()


def _role_id(db: Session, user) -> int:
    """把 account.id 解析为业务实体 id（teacher.id / student.id）。"""
    account = db.get(Account, user.user_id)
    return account.role_id if account else user.user_id


def _ensure_not_student(request: Request) -> None:
    """面板端点仅限教师+：学生角色显式拒绝（spec「学生访问面板」）。"""
    if request.state.user.role == "student":
        raise ForbiddenError()


def _require_class_in_teacher_school(db: Session, request: Request, class_id: int) -> None:
    """教师仅可访问本校班级（组织链隔离）；admin+ 不限。班级不存在 → 404，跨校 → 403。"""
    cls = db.get(Class, class_id)
    if cls is None:
        raise NotFoundError()
    if request.state.user.role != "teacher":
        return
    grade = db.get(Grade, cls.grade_id)
    teacher = db.get(Teacher, _role_id(db, request.state.user))
    if grade is None or teacher is None or grade.school_id != teacher.school_id:
        raise ForbiddenError()


def _require_own_dashboard(db: Session, request: Request, teacher_id: int) -> None:
    """教师仅可查看自身首页概览；admin+ 不限（沿用诊断 _require_own_config 模式）。"""
    if request.state.user.role == "teacher" and _role_id(db, request.state.user) != teacher_id:
        raise ForbiddenError()


@panel_router.get("/class/{class_id}")
@require_permission("analysis", "read")
def class_panel(
    request: Request, class_id: int, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    _require_class_in_teacher_school(db, request, class_id)
    return load_class_panel(db, class_id)


@panel_router.get("/class/{class_id}/knowledge/{knowledge_point}")
@require_permission("analysis", "read")
def knowledge_detail(
    request: Request, class_id: int, knowledge_point: str, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    _require_class_in_teacher_school(db, request, class_id)
    return load_knowledge_detail(db, class_id, knowledge_point)


@panel_router.get("/class/{class_id}/student/{student_id}")
@require_permission("analysis", "read")
def student_detail(
    request: Request, class_id: int, student_id: int, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    _require_class_in_teacher_school(db, request, class_id)
    return load_student_detail(db, class_id, student_id)


@panel_router.get("/class/{class_id}/trend")
@require_permission("analysis", "read")
def class_trend(
    request: Request, class_id: int, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    _require_class_in_teacher_school(db, request, class_id)
    return load_trend(db, class_id)


@panel_router.get("/export/{class_id}")
@require_permission("analysis", "read")
def export_panel(
    request: Request, class_id: int, db: Session = Depends(get_db)
) -> StreamingResponse:
    _ensure_not_student(request)
    _require_class_in_teacher_school(db, request, class_id)
    panel = load_class_panel(db, class_id)
    pdf = pdf_bytes(panel_report_html(panel))
    if pdf is None:
        raise HTTPException(status_code=500, detail="PDF 转换失败")
    name = panel["class_overview"]["class_name"]
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="panel-{class_id}.pdf"'},
    )


@panel_router.get("/dashboard/{teacher_id}")
@require_permission("analysis", "read")
def dashboard(
    request: Request, teacher_id: int, db: Session = Depends(get_db)
) -> dict:
    _ensure_not_student(request)
    _require_own_dashboard(db, request, teacher_id)
    return load_dashboard(db, teacher_id)
