"""Critical P0 pair-space invite flow tests."""
import time

from app.core.database import ChatORM, ChatParticipantORM, PrivateSpaceInviteORM


def _register(client, phone, username):
    response = client.post("/api/auth/register", json={"phone": phone, "username": username, "password": "123456"})
    assert response.status_code in (200, 201)
    return response.json()["user"], response.cookies.get("access_token")


def test_invite_preview_accept_and_one_time_reuse(client):
    creator, creator_token = _register(client, "+19900000001", "space_creator")
    client.cookies.set("access_token", creator_token)
    created = client.post("/api/spaces/invites", json={})
    assert created.status_code == 201
    token = created.json()["invite_url"].rsplit("/", 1)[1]
    preview = client.get(f"/api/spaces/invites/{token}/preview")
    assert preview.status_code == 200
    assert preview.json()["status"] == "active"
    assert preview.json()["creator"]["display_name"] == "space_creator"
    assert client.post(f"/api/spaces/invites/{token}/accept").status_code == 400
    joiner, joiner_token = _register(client, "+19900000002", "space_joiner")
    client.cookies.set("access_token", joiner_token)
    accepted = client.post(f"/api/spaces/invites/{token}/accept")
    assert accepted.status_code == 200
    chat_id = accepted.json()["chat_id"]
    assert client.post(f"/api/spaces/invites/{token}/accept").status_code == 409
    space = client.get(f"/api/spaces/{chat_id}")
    assert space.status_code == 200
    assert space.json()["stats"]["days"] == 1


def test_expired_and_revoked_invites_are_not_publicly_accepted(auth_client, db_session):
    created = auth_client.post("/api/spaces/invites", json={})
    token = created.json()["invite_url"].rsplit("/", 1)[1]
    invite = db_session.query(PrivateSpaceInviteORM).first()
    invite.expires_at = int(time.time()) - 1
    db_session.commit()
    assert auth_client.get(f"/api/spaces/invites/{token}/preview").json()["status"] == "expired"


def test_invalid_and_revoked_invites_are_safe(client, auth_client):
    assert client.get("/api/spaces/invites/not-a-real-token/preview").json()["status"] == "invalid"
    created = auth_client.post("/api/spaces/invites", json={})
    assert created.status_code == 201
    invite_id = created.json()["id"]
    token = created.json()["invite_url"].rsplit("/", 1)[1]
    assert auth_client.delete(f"/api/spaces/invites/{invite_id}").status_code == 204
    assert client.get(f"/api/spaces/invites/{token}/preview").json()["status"] == "revoked"


def test_group_and_non_participant_cannot_use_space(client):
    owner, owner_token = _register(client, "+19900000011", "space_owner")
    stranger, stranger_token = _register(client, "+19900000012", "space_stranger")
    client.cookies.set("access_token", owner_token)
    group = client.post("/api/chats/group", json={"name": "No space", "participant_ids": ["space_owner", "space_stranger"]})
    assert group.status_code == 201
    assert client.post(f"/api/spaces/{group.json()['id']}/activate", json={}).status_code == 404
    client.cookies.set("access_token", stranger_token)
    assert client.get(f"/api/spaces/{group.json()['id']}").status_code in (403, 404)


def test_existing_private_chat_is_reused_for_invite(client):
    creator, creator_token = _register(client, "+19900000003", "pair_creator")
    joiner, joiner_token = _register(client, "+19900000004", "pair_joiner")
    client.cookies.set("access_token", creator_token)
    existing = client.post("/api/chats/private", json={"username": "pair_joiner"})
    assert existing.status_code == 201
    existing_id = existing.json()["id"]
    token = client.post("/api/spaces/invites", json={}).json()["invite_url"].rsplit("/", 1)[1]
    client.cookies.set("access_token", joiner_token)
    assert client.post(f"/api/spaces/invites/{token}/accept").json()["chat_id"] == existing_id


def test_private_chat_space_stays_pending_until_the_other_member_accepts(client):
    creator, creator_token = _register(client, "+19900000021", "pending_creator")
    joiner, joiner_token = _register(client, "+19900000022", "pending_joiner")
    client.cookies.set("access_token", creator_token)
    chat = client.post("/api/chats/private", json={"username": "pending_joiner"}).json()
    created = client.post("/api/spaces/invites", json={"chat_id": chat["id"]})
    assert created.status_code == 201
    assert client.get(f"/api/spaces/{chat['id']}/state").json()["status"] == "pending"
    assert client.get(f"/api/spaces/{chat['id']}").status_code == 404
    client.cookies.set("access_token", joiner_token)
    state = client.get(f"/api/spaces/{chat['id']}/state").json()
    assert state["status"] == "pending" and state["can_accept"] is True
    assert client.post(f"/api/spaces/{chat['id']}/accept-pending").status_code == 200
    assert client.get(f"/api/spaces/{chat['id']}/state").json()["status"] == "active"
    assert client.get(f"/api/spaces/{chat['id']}").status_code == 200


def test_space_dates_and_saved_messages_crud(auth_client, client):
    """Dates and saved messages stay scoped to an active private space."""
    _register(client, "+19900000031", "space_crud_friend")
    chat = auth_client.post("/api/chats/private", json={"username": "space_crud_friend"})
    assert chat.status_code == 201
    chat_id = chat.json()["id"]
    assert auth_client.post(f"/api/spaces/{chat_id}/activate", json={}).status_code == 200

    created = auth_client.post(f"/api/spaces/{chat_id}/dates", json={
        "title": "Начало общения", "event_date": "2026-03-31", "emoji": "❤️", "repeats_yearly": True,
    })
    assert created.status_code == 201
    date_id = created.json()["id"]
    updated = auth_client.put(f"/api/spaces/{chat_id}/dates/{date_id}", json={
        "title": "Познакомились", "event_date": "2026-03-31", "emoji": "✨", "repeats_yearly": False,
    })
    assert updated.status_code == 200 and updated.json()["repeats_yearly"] is False
    assert auth_client.delete(f"/api/spaces/{chat_id}/dates/{date_id}").status_code == 204
    assert auth_client.get(f"/api/spaces/{chat_id}/dates").json() == []

    message = auth_client.post(f"/api/chats/{chat_id}/messages", json={"content": "Важное текстовое сообщение"})
    assert message.status_code == 200
    message_id = message.json()["id"]
    assert auth_client.post(f"/api/spaces/{chat_id}/memories/{message_id}").status_code == 201
    # Saving twice is intentionally idempotent rather than creating a duplicate.
    assert auth_client.post(f"/api/spaces/{chat_id}/memories/{message_id}").status_code == 201
    saved = auth_client.get(f"/api/spaces/{chat_id}/memories").json()
    assert len(saved) == 1 and saved[0]["content"] == "Важное текстовое сообщение"
    assert auth_client.delete(f"/api/spaces/{chat_id}/memories/{message_id}").status_code == 204
    assert auth_client.get(f"/api/spaces/{chat_id}/memories").json() == []
