"""JWT 六场景 + PBKDF2 测试（7.1-7.3）。

六类场景（F6）：编解码 / 签名 / 过期 / 篡改 / 格式畸形 / access-refresh 互用。
"""
import hmac
import time

import pytest

from app.core.security import (
    JWT_SECRET,
    InvalidTokenError,
    TokenExpiredError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


def _split(token: str) -> tuple[str, str, str]:
    header_b64, payload_b64, signature = token.split(".")
    return header_b64, payload_b64, signature


def _tamper_payload(token: str) -> str:
    header_b64, payload_b64, signature = _split(token)
    # 翻转 payload 末尾字符，必然改变内容
    flipped = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B")
    return f"{header_b64}.{flipped}.{signature}"


# ---- 7.2 六场景 ----

def test_roundtrip_encode_decode():
    token = create_token(user_id=1, role="teacher", school_id=2)
    payload = decode_token(token)
    assert payload["user_id"] == 1
    assert payload["role"] == "teacher"
    assert payload["school_id"] == 2
    assert payload["type"] == "access"


def test_refresh_token_roundtrip():
    token = create_token(user_id=5, role="student", token_type="refresh", ttl=7 * 24 * 3600)
    payload = decode_token(token, expected_type="refresh")
    assert payload["type"] == "refresh"


def test_signature_verification_fails_on_wrong_secret():
    token = create_token(user_id=1, role="teacher", secret="correct-secret")
    with pytest.raises(InvalidTokenError):
        decode_token(token, secret="wrong-secret")


def test_expired_token_rejected():
    token = create_token(user_id=1, role="teacher", ttl=-10)
    with pytest.raises(TokenExpiredError):
        decode_token(token)


def test_tampered_payload_rejected():
    token = create_token(user_id=1, role="teacher")
    with pytest.raises(InvalidTokenError):
        decode_token(_tamper_payload(token))


@pytest.mark.parametrize(
    "bad_token",
    [
        "not-a-jwt",
        "only.two",
        "a.b.c.d",
        "",
        "abc.def.ghi",  # 三个合法段但无法解码为 JSON
    ],
)
def test_malformed_token_rejected(bad_token):
    with pytest.raises(InvalidTokenError):
        decode_token(bad_token)


def test_access_token_cannot_be_used_as_refresh():
    access = create_token(user_id=1, role="teacher", token_type="access")
    with pytest.raises(InvalidTokenError):
        decode_token(access, expected_type="refresh")


def test_refresh_token_cannot_be_used_as_access():
    refresh = create_token(user_id=1, role="teacher", token_type="refresh")
    with pytest.raises(InvalidTokenError):
        decode_token(refresh, expected_type="access")


def test_alg_header_must_be_hs256():
    token = create_token(user_id=1, role="teacher")
    header_b64, payload_b64, signature = _split(token)
    import base64
    import json

    header = json.loads(base64.urlsafe_b64decode(header_b64 + "=="))
    header["alg"] = "none"
    forged_header = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    forged = f"{forged_header}.{payload_b64}.{signature}"
    with pytest.raises(InvalidTokenError):
        decode_token(forged)


def test_exp_within_ttl_boundary():
    token = create_token(user_id=1, role="teacher", ttl=60)
    payload = decode_token(token)
    assert payload["exp"] > time.time()


# ---- 7.3 PBKDF2 ----

def test_hash_and_verify_correct_password():
    hashed = hash_password("ChemAI@2026")
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("ChemAI@2026", hashed) is True


def test_verify_wrong_password_fails():
    hashed = hash_password("right-password")
    assert verify_password("wrong-password", hashed) is False


def test_hash_produces_unique_salt():
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2
    assert verify_password("same-password", h1) is True
    assert verify_password("same-password", h2) is True


def test_verify_malformed_stored_hash_returns_false():
    assert verify_password("x", "not-a-valid-format") is False
    assert verify_password("x", "") is False
