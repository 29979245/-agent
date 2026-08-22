"""JWT（HMAC-SHA256 自实现，纯 stdlib）与 PBKDF2 密码哈希（D3/D4）。

设计文档 23 §8：access 24h + refresh 7d，无状态会话。
密钥取自环境变量 JWT_SECRET（D3），rotation = 换密钥重启（§9.3）。
"""
import base64
import hashlib
import hmac
import json
import os
import time

_jwt_secret = os.environ.get("JWT_SECRET")
if not _jwt_secret:
    raise RuntimeError(
        "JWT_SECRET 环境变量未设置：签名密钥必须显式配置（D3），拒绝使用默认密钥上线。"
    )
JWT_SECRET = _jwt_secret
ACCESS_TOKEN_TTL = 24 * 3600
REFRESH_TOKEN_TTL = 7 * 24 * 3600

_PBKDF2_ITERATIONS = 100_000


class JWTError(Exception):
    pass


class TokenExpiredError(JWTError):
    pass


class InvalidTokenError(JWTError):
    pass


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(header_b64: str, payload_b64: str, secret: str) -> str:
    message = f"{header_b64}.{payload_b64}".encode("ascii")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return _b64url_encode(digest)


def create_token(
    user_id: int,
    role: str,
    school_id: int | None = None,
    token_type: str = "access",
    ttl: int = ACCESS_TOKEN_TTL,
    secret: str = JWT_SECRET,
) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "user_id": user_id,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
    }
    if school_id is not None:
        payload["school_id"] = school_id
    header_b64 = _b64url_encode(json.dumps(header).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload).encode("utf-8"))
    return f"{header_b64}.{payload_b64}.{_sign(header_b64, payload_b64, secret)}"


def decode_token(
    token: str,
    expected_type: str = "access",
    secret: str = JWT_SECRET,
) -> dict:
    """验证签名/过期/格式/type 混淆，返回 payload。

    六类场景（F6）：编解码 / 签名 / 过期 / 篡改 / 格式畸形 / access-refresh 互用。
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidTokenError("格式畸形")
    header_b64, payload_b64, signature = parts
    expected = _sign(header_b64, payload_b64, secret)
    if not hmac.compare_digest(expected, signature):
        raise InvalidTokenError("签名校验失败")
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        raise InvalidTokenError("格式畸形")
    if header.get("alg") != "HS256":
        raise InvalidTokenError("非法算法")
    if payload.get("type") != expected_type:
        raise InvalidTokenError("令牌类型不匹配")
    if int(payload.get("exp", 0)) < time.time():
        raise TokenExpiredError("令牌已过期")
    return payload


def hash_password(password: str, iterations: int = _PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False
