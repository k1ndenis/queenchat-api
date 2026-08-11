import time

from app.core.database import (
    ChatORM, ChatParticipantORM, MessageORM, PrivateChatInviteORM,
    PrivateSpaceInviteORM, UserORM,
)
from app.core.security import create_token


def make_admin(db, user_id):
    user = db.get(UserORM, user_id)
    user.role = "admin"
    db.commit()


def test_non_admin_is_forbidden(auth_client):
    assert auth_client.get("/api/admin/dashboard").status_code == 403
    assert auth_client.get("/api/admin/analytics?period=7d").status_code == 403


def test_analytics_periods_zero_buckets_totals_and_validation(auth_client, db_session):
    make_admin(db_session, auth_client.user_id)
    now = int(time.time())
    # Put real data into one of the seven UTC day buckets. The remaining buckets
    # must still be returned as explicit zeros.
    created = now - 3 * 86400
    user = UserORM(id="analytics-user", username="analytics", phone="+10000000019", password_hash="x", display_name="Analytics", created_at=created)
    chat = ChatORM(id="analytics-chat", name="Analytics", chat_type="group", is_group=True, created_by=auth_client.user_id, created_at=created, updated_at=created)
    message = MessageORM(id="analytics-message", chat_id=chat.id, sender_id=auth_client.user_id, content="x", created_at=created, is_read=False)
    db_session.add_all([user, chat, message]); db_session.commit()

    for period, count, granularity in (("24h", 24, "hour"), ("7d", 7, "day"), ("30d", 30, "day"), ("1y", 12, "month")):
        response = auth_client.get(f"/api/admin/analytics?period={period}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["period"] == period and payload["granularity"] == granularity
        assert len(payload["points"]) == count
        assert payload["totals"]["registrations"] == sum(point["registrations"] for point in payload["points"])
        assert payload["totals"]["messages"] == sum(point["messages"] for point in payload["points"])

    seven_days = auth_client.get("/api/admin/analytics?period=7d").json()
    assert any(point["registrations"] == 0 and point["messages"] == 0 for point in seven_days["points"])
    assert auth_client.get("/api/admin/analytics?period=bad").status_code == 422


def test_admin_dashboard_and_user_controls(auth_client, db_session):
    make_admin(db_session, auth_client.user_id)
    assert auth_client.get("/api/admin/dashboard").status_code == 200

    target = UserORM(id="admin-target", username="target", phone="+10000000009", password_hash="x", display_name="Target", created_at=int(time.time()))
    db_session.add(target); db_session.commit()
    assert auth_client.get("/api/admin/users?q=target").json()["total"] == 1
    assert auth_client.post("/api/admin/users/admin-target/block", json={"reason": "test"}).status_code == 200
    # A previously-issued target token is rejected on its next protected request.
    auth_client.cookies.set("access_token", create_token(target.id, target.username))
    assert auth_client.get("/api/auth/me").status_code == 403
    login = auth_client.post("/api/auth/login", json={"phone": "+10000000001", "password": "123456"})
    auth_client.cookies.set("access_token", login.cookies.get("access_token"))
    assert auth_client.post("/api/admin/users/admin-target/unblock").status_code == 200
    assert auth_client.patch("/api/admin/users/admin-target/role", json={"role": "admin"}).status_code == 200
    assert auth_client.get("/api/admin/audit").json()["total"] >= 3


def test_last_admin_and_self_delete_are_protected(auth_client, db_session):
    make_admin(db_session, auth_client.user_id)
    assert auth_client.patch(f"/api/admin/users/{auth_client.user_id}/role", json={"role": "user"}).status_code == 400
    assert auth_client.request("DELETE", f"/api/admin/users/{auth_client.user_id}", json={"username": "testuser"}).status_code == 400


def test_chat_delete_and_pagination_bound(auth_client, db_session):
    make_admin(db_session, auth_client.user_id)
    chat = ChatORM(id="admin-chat", name="Admin test", chat_type="group", is_group=True, created_by=auth_client.user_id, created_at=1, updated_at=1)
    db_session.add(chat); db_session.add(ChatParticipantORM(id="admin-participant", chat_id=chat.id, user_id=auth_client.user_id, joined_at=1)); db_session.add(MessageORM(id="admin-message", chat_id=chat.id, sender_id=auth_client.user_id, content="x", created_at=1, is_read=False)); db_session.commit()
    assert auth_client.get("/api/admin/users?page_size=999").json()["page_size"] == 100
    assert auth_client.get("/api/admin/chats").status_code == 200
    assert auth_client.request("DELETE", "/api/admin/chats/admin-chat", json={"confirmation": "DELETE"}).status_code == 200
    assert db_session.get(ChatORM, "admin-chat") is None


def test_admin_operational_lists_are_paged_and_hide_invite_tokens(auth_client, db_session):
    make_admin(db_session, auth_client.user_id)
    now = int(time.time())
    db_session.add_all([
        PrivateChatInviteORM(id="admin-chat-invite", token_hash="do-not-expose-chat", creator_user_id=auth_client.user_id, created_at=now, expires_at=now + 3600),
        PrivateSpaceInviteORM(id="admin-space-invite", token_hash="do-not-expose-space", creator_user_id=auth_client.user_id, created_at=now + 1, expires_at=now + 3600),
    ])
    db_session.commit()

    invites = auth_client.get("/api/admin/invites?page=1&page_size=1")
    assert invites.status_code == 200
    payload = invites.json()
    assert payload["total"] == 2 and len(payload["items"]) == 1
    assert "token" not in str(payload).lower()
    assert auth_client.get("/api/admin/messages?page_size=101").json()["page_size"] == 100
    assert auth_client.get("/api/admin/files?page_size=10").status_code == 200
    assert auth_client.get("/api/admin/realtime").status_code == 200


def test_user_delete_requires_target_username(auth_client, db_session):
    make_admin(db_session, auth_client.user_id)
    target = UserORM(id="delete-confirm-target", username="target-confirm", phone="+10000000077", password_hash="x", display_name="Target", created_at=int(time.time()))
    db_session.add(target); db_session.commit()
    response = auth_client.request("DELETE", "/api/admin/users/delete-confirm-target", json={"username": "wrong-user"})
    assert response.status_code == 400
    assert db_session.get(UserORM, "delete-confirm-target") is not None


def test_admin_extended_analytics_storage_security_and_custom_range(auth_client, db_session):
    make_admin(db_session, auth_client.user_id)
    now = int(time.time())
    response = auth_client.get("/api/admin/analytics?period=90d")
    assert response.status_code == 200 and len(response.json()["points"]) == 90
    custom = auth_client.get(f"/api/admin/analytics?period=custom&date_from={now - 3 * 86400}&date_to={now}")
    assert custom.status_code == 200 and custom.json()["granularity"] == "day"
    assert auth_client.get(f"/api/admin/analytics?period=custom&date_from=1&date_to={400 * 86400}").status_code == 422
    storage = auth_client.get("/api/admin/storage")
    assert storage.status_code == 200 and "disk" in storage.json() and "file_path" not in str(storage.json())
    security = auth_client.get("/api/admin/security")
    assert security.status_code == 200 and any(policy["name"] == "login-ip" for policy in security.json()["policies"])
