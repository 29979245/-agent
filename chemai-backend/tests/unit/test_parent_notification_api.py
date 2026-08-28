"""change parent-backend 2.7：通知 API。

覆盖：分页倒序 / 越权 parent_id 被忽略（取自 token）/ 本人已读 / 标记他人通知 404。
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token, hash_password
from app.db.models import Account, Parent, ParentNotification
from app.db.models.enums import AccountRole, NotificationType
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def env(db_session):
    parent = Parent(name="张父", phone="13900000001")
    db_session.add(parent)
    db_session.flush()
    account = Account(
        username=f"parent_{parent.id}",
        password_hash=hash_password("x"),
        role=AccountRole.parent,
        role_id=parent.id,
    )
    db_session.add(account)
    db_session.flush()
    db_session.commit()
    return {"parent_id": parent.id, "account_id": account.id}


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    def _teardown():
        app.dependency_overrides.pop(get_db, None)

    with TestClient(app) as c:
        yield c
    _teardown()


def _token(env):
    return {"Authorization": f"Bearer {create_token(env['account_id'], AccountRole.parent.value)}"}


def _notify(db_session, parent_id, i, day):
    n = ParentNotification(
        parent_id=parent_id,
        notification_type=NotificationType.daily_report,
        title=f"通知 {i}",
        content=f"内容 {i}",
        created_at=datetime.datetime.combine(day, datetime.time(9, 0)),
    )
    db_session.add(n)
    db_session.flush()
    return n


# ---------------- 分页 + 排序 ----------------

def test_notifications_paginated_desc(client, db_session, env):
    for i in range(5):
        _notify(db_session, env["parent_id"], i, datetime.date(2026, 8, 20 + i))
    db_session.commit()
    resp = client.get("/api/parent/notifications", headers=_token(env))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    titles = [n["title"] for n in body["notifications"]]
    assert titles == ["通知 4", "通知 3", "通知 2", "通知 1", "通知 0"]  # created_at 倒序
    # 分页
    page = client.get("/api/parent/notifications?limit=2&offset=1", headers=_token(env))
    assert [n["title"] for n in page.json()["notifications"]] == ["通知 3", "通知 2"]


def test_notifications_empty(client, db_session, env):
    db_session.commit()
    resp = client.get("/api/parent/notifications", headers=_token(env))
    assert resp.status_code == 200
    body = resp.json()
    assert body["notifications"] == []
    assert body["total"] == 0


# ---------------- 越权 parent_id 被忽略 ----------------

def test_notifications_query_parent_id_ignored(client, db_session, env):
    # 另一个家长的通知
    other = Parent(name="李母", phone="13900000002")
    db_session.add(other)
    db_session.flush()
    _notify(db_session, other.id, 99, datetime.date(2026, 8, 27))
    _notify(db_session, env["parent_id"], 1, datetime.date(2026, 8, 26))
    db_session.commit()
    # 即使 query 带他人 parent_id，也只返回 token 家长自己的通知
    resp = client.get(f"/api/parent/notifications?parent_id={other.id}", headers=_token(env))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["notifications"][0]["title"] == "通知 1"


# ---------------- 标记已读 ----------------

def test_notification_mark_read_own(client, db_session, env):
    n = _notify(db_session, env["parent_id"], 1, datetime.date(2026, 8, 27))
    db_session.commit()
    resp = client.put(
        f"/api/parent/notifications/{n.id}/read",
        headers=_token(env),
    )
    assert resp.status_code == 200
    db_session.refresh(n)
    assert n.is_read is True
    body = resp.json()
    assert body["is_read"] is True


def test_notification_mark_other_read_404(client, db_session, env):
    other = Parent(name="李母", phone="13900000002")
    db_session.add(other)
    db_session.flush()
    n = _notify(db_session, other.id, 99, datetime.date(2026, 8, 27))
    db_session.commit()
    resp = client.put(f"/api/parent/notifications/{n.id}/read", headers=_token(env))
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "NOTIFICATION_NOT_FOUND"


def test_notification_mark_missing_404(client, db_session, env):
    resp = client.put("/api/parent/notifications/999999/read", headers=_token(env))
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "NOTIFICATION_NOT_FOUND"
