"""学生账户自服务 API（doc 52「我的」页绑定码，挂载于 /api/student）。

POST /{student_id}/bind-code 生成/重新生成 6 位家长绑定码写回 Student.bind_code，
学生仅可操作自身（self-check 403）。
"""
import secrets

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import require_permission
from app.db.models import Account, Student
from app.db.session import get_db

student_router = APIRouter()

# 大写字母数字集，排除易混淆字符 0/O、1/I
_BIND_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_BIND_CODE_LENGTH = 6


def _role_id(db: Session, user) -> int:
    """把 account.id 解析为业务实体 id（student.id）。"""
    account = db.get(Account, user.user_id)
    return account.role_id if account else user.user_id


def _generate_bind_code() -> str:
    return "".join(secrets.choice(_BIND_CODE_ALPHABET) for _ in range(_BIND_CODE_LENGTH))


@student_router.post("/{student_id}/bind-code")
@require_permission("report", "update")
def generate_bind_code(request: Request, student_id: int, db: Session = Depends(get_db)) -> dict:
    user = request.state.user
    if user.role == "student" and _role_id(db, user) != student_id:
        raise ForbiddenError()  # 学生仅可操作自身绑定码
    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError(detail="学生不存在", error_code="STUDENT_NOT_FOUND")
    student.bind_code = _generate_bind_code()
    db.commit()
    return {"bind_code": student.bind_code}
