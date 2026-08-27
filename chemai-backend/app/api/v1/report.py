"""学生个人报告 API（doc 52「我的」页，挂载于 /api/report）。

GET /student/{student_id} 聚合返回 profile/stats/weekly/learning_plan，
学生仅可读自身（self-check 403），教师/组长按矩阵读取任意学生。
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError
from app.core.permissions import require_permission
from app.db.models import Account
from app.db.session import get_db
from app.services.analytics.report_service import build_student_report

report_router = APIRouter()


def _role_id(db: Session, user) -> int:
    """把 account.id 解析为业务实体 id（student.id）。"""
    account = db.get(Account, user.user_id)
    return account.role_id if account else user.user_id


@report_router.get("/student/{student_id}")
@require_permission("report", "read")
def student_report(request: Request, student_id: int, db: Session = Depends(get_db)) -> dict:
    user = request.state.user
    if user.role == "student" and _role_id(db, user) != student_id:
        raise ForbiddenError()  # 学生仅可读自身报告
    return build_student_report(db, student_id)
