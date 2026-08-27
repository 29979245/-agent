"""统一错误码（9.2）：401/403/404/422/500 + detail/error_code/suggestion。

响应体统一为 {"detail": ..., "error_code": ..., "suggestion": ...}。
"""
from fastapi import HTTPException


class APIException(HTTPException):
    def __init__(self, status_code: int, detail: str, error_code: str, suggestion: str = ""):
        super().__init__(
            status_code=status_code,
            detail={"detail": detail, "error_code": error_code, "suggestion": suggestion},
        )


class UnauthorizedError(APIException):
    """401：认证失败——凭据错误/令牌无效/令牌类型不匹配。"""

    def __init__(self, detail="认证失败，请重新登录", error_code="UNAUTHORIZED", suggestion="请确认用户名与密码后重试"):
        super().__init__(401, detail, error_code, suggestion)


class TokenExpiredError(APIException):
    """401：令牌过期。"""

    def __init__(self, detail="令牌已过期", error_code="TOKEN_EXPIRED", suggestion="请使用 refresh token 刷新或重新登录"):
        super().__init__(401, detail, error_code, suggestion)


class ForbiddenError(APIException):
    """403：无权限执行该操作。"""

    def __init__(self, detail="没有权限执行此操作", error_code="PERMISSION_DENIED", suggestion="请联系管理员确认账号权限"):
        super().__init__(403, detail, error_code, suggestion)


class AccountPendingError(APIException):
    """403：教师账号待审核，拒绝登录（F4）。"""

    def __init__(self, detail="账号待审核，暂无法登录", error_code="ACCOUNT_PENDING", suggestion="请联系学校管理员完成入驻审核"):
        super().__init__(403, detail, error_code, suggestion)


class AccountRejectedError(APIException):
    """403：教师账号被驳回，拒绝登录。"""

    def __init__(self, detail="账号已驳回，无法登录", error_code="ACCOUNT_REJECTED", suggestion="请联系学校管理员核实入驻信息"):
        super().__init__(403, detail, error_code, suggestion)


class BindCodeInvalidError(APIException):
    """403：手机号或绑定码不匹配（业务规则冲突）。"""

    def __init__(self, detail="手机号或绑定码不匹配", error_code="BIND_CODE_INVALID", suggestion="请核对手机号与学生绑定码后重试"):
        super().__init__(403, detail, error_code, suggestion)


class BusinessRuleViolationError(APIException):
    """400：业务规则冲突——旧密码不正确等输入态校验失败。"""

    def __init__(self, detail="业务规则冲突", error_code="BUSINESS_RULE_VIOLATION", suggestion="请核对输入后重试"):
        super().__init__(400, detail, error_code, suggestion)


class NotFoundError(APIException):
    """404：资源不存在。"""

    def __init__(self, detail="资源不存在", error_code="NOT_FOUND", suggestion=""):
        super().__init__(404, detail, error_code, suggestion)


class RequestValidationError(APIException):
    """422：请求参数校验失败。"""

    def __init__(self, detail="请求参数校验失败", error_code="VALIDATION_ERROR", suggestion="请检查请求参数格式"):
        super().__init__(422, detail, error_code, suggestion)


class ServerError(APIException):
    """500：服务器内部错误。"""

    def __init__(self, detail="服务器内部错误", error_code="INTERNAL_ERROR", suggestion="请稍后重试或联系管理员"):
        super().__init__(500, detail, error_code, suggestion)
