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
