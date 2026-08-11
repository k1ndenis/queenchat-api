def _register(client, phone, username):
    response = client.post("/api/auth/register", json={"phone": phone, "username": username, "password": "123456"})
    assert response.status_code in (200, 201)
    return response.json()["user"], response.cookies.get("access_token")


def test_chat_invite_creates_or_reuses_private_chat_without_space(client):
    creator, creator_token = _register(client, "+19900000101", "chat_inviter")
    client.cookies.set("access_token", creator_token)
    created = client.post("/api/chats/invites")
    assert created.status_code == 201
    token = created.json()["invite_url"].rsplit("/", 1)[1]
    assert client.get(f"/api/chats/invites/{token}/preview").json()["creator"]["display_name"] == "chat_inviter"
    joiner, joiner_token = _register(client, "+19900000102", "chat_joiner")
    client.cookies.set("access_token", joiner_token)
    accepted = client.post(f"/api/chats/invites/{token}/accept")
    assert accepted.status_code == 200
    chat_id = accepted.json()["chat_id"]
    assert client.post(f"/api/chats/invites/{token}/accept").status_code == 409
    assert client.get(f"/api/spaces/{chat_id}/state").json()["status"] == "not_created"
    assert client.get(f"/api/spaces/{chat_id}").status_code == 404
