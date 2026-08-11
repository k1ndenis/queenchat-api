import pytest
from fastapi.testclient import TestClient
from main import app
from unittest.mock import patch, AsyncMock

class TestChatAPI:
    @pytest.fixture(autouse=True)
    def setup(self, client):
        reg1 = client.post(
            "/api/auth/register",
            json={"phone": "+10000001001", "username": "chatuser", "password": "123456"}
        )
        if reg1.status_code == 200:
            self.access_token = reg1.cookies.get("access_token")
            self.user_id = reg1.json()["user"]["id"]
        else:
            login = client.post(
                "/api/auth/login",
                json={"phone": "+10000001001", "password": "123456"}
            )
            self.access_token = login.cookies.get("access_token")
            self.user_id = login.json()["user"]["id"]

        reg2 = client.post(
            "/api/auth/register",
            json={"phone": "+10000001002", "username": "otheruser", "password": "123456"}
        )
        if reg2.status_code == 200:
            self.other_user_id = reg2.json()["user"]["id"]
            self.other_token = reg2.cookies.get("access_token")
        else:
            login2 = client.post(
                "/api/auth/login",
                json={"phone": "+10000001002", "password": "123456"}
            )
            self.other_user_id = login2.json()["user"]["id"]
            self.other_token = login2.cookies.get("access_token")

        client.cookies.set("access_token", self.access_token)
        self.client = client

    @patch('app.services.chat_service.redis_cache')
    def test_create_private_chat_success(self, mock_redis):
        mock_redis.get.return_value = None
        mock_redis.delete.return_value = None
        mock_redis.set.return_value = None
        
        response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["otheruser"]}
        )
        print(f"Response status: {response.status_code}")
        print(f"Response body: {response.text}")
        assert response.status_code == 201
        data = response.json()
        assert data["is_group"] is False

    def test_create_chat_missing_username(self):
        response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": []}
        )
        assert response.status_code == 400
        assert "Username required" in response.text

    def test_create_chat_user_not_found(self):
        response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["nonexistent"]}
        )
        assert response.status_code == 404
        assert "User 'nonexistent' not found" in response.text

    def test_mark_all_messages_as_read_invalid_chat(self, auth_client):
        response = auth_client.post("/api/chats/invalid-id/messages/read/all")
        assert response.status_code == 404
        assert "Chat not found" in response.text or "Invalid chat ID" in response.text

    def test_create_chat_user_not_found(self):
        response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["nonexistent"]}
        )
        assert response.status_code == 404
        assert "User 'nonexistent' not found" in response.text

    def test_get_user_chats(self):
        self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["otheruser"]}
        )

        response = self.client.get("/api/chats/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_chat_by_id(self):
        create_response = self.client.post("/api/chats/private", json={"username": "otheruser"})
        assert create_response.status_code == 201
        chat_id = create_response.json()["id"]

        response = self.client.get(f"/api/chats/{chat_id}")
        assert response.status_code == 200
        assert response.json()["id"] == chat_id

    def test_get_chat_not_found(self):
        response = self.client.get("/api/chats/nonexistent-id")
        assert response.status_code == 404
        assert "Chat not found" in response.text

    def test_delete_chat_success(self):
        create_response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["otheruser"]}
        )
        assert create_response.status_code == 201
        chat_id = create_response.json()["id"]

        response = self.client.delete(f"/api/chats/{chat_id}")
        assert response.status_code == 200
        assert response.json()["id"] == chat_id

    def test_send_message(self):
        create_response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["otheruser"]}
        )
        assert create_response.status_code == 201
        chat_id = create_response.json()["id"]

        response = self.client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Hello, world!"}
        )
        assert response.status_code == 200
        data = response.json()
    
    def test_send_message_legacy_fcm_key_and_push_failure_do_not_return_500(self, monkeypatch):
        from app.api.v1 import chats
        from app.api.v1 import notifications

        class FakeRedis:
            def __init__(self):
                self.store = {}

            def hvals(self, name):
                value = self.store.get(name)
                if isinstance(value, str):
                    raise notifications.redis.exceptions.ResponseError(
                        "WRONGTYPE Operation against a key holding the wrong kind of value"
                    )
                return list((value or {}).values())

            def get(self, name):
                value = self.store.get(name)
                return value if isinstance(value, str) else None

            def hset(self, name, field, value):
                if isinstance(self.store.get(name), str):
                    raise notifications.redis.exceptions.ResponseError(
                        "WRONGTYPE Operation against a key holding the wrong kind of value"
                    )
                self.store.setdefault(name, {})[field] = value

            def hdel(self, name, field):
                value = self.store.get(name)
                if isinstance(value, str):
                    raise notifications.redis.exceptions.ResponseError(
                        "WRONGTYPE Operation against a key holding the wrong kind of value"
                    )
                if isinstance(value, dict):
                    value.pop(field, None)

            def hgetall(self, name):
                value = self.store.get(name)
                if isinstance(value, str):
                    raise notifications.redis.exceptions.ResponseError(
                        "WRONGTYPE Operation against a key holding the wrong kind of value"
                    )
                return value or {}

            def expire(self, name, ttl):
                return True

            def delete(self, name):
                self.store.pop(name, None)

        fake_redis = FakeRedis()
        fake_redis.store[f"fcm:{self.other_user_id}"] = "legacy-token"

        monkeypatch.setattr(notifications, "TESTING", False)
        monkeypatch.setattr(notifications, "REDIS_AVAILABLE", True)
        monkeypatch.setattr(notifications, "redis_client", fake_redis)
        monkeypatch.setattr(notifications.messaging, "send", lambda message: (_ for _ in ()).throw(RuntimeError("fcm down")))
        broadcast_mock = AsyncMock()
        monkeypatch.setattr(chats.manager, "broadcast_to_chat", broadcast_mock)

        create_response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["otheruser"]}
        )
        assert create_response.status_code == 201
        chat_id = create_response.json()["id"]

        response = self.client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Hello, world!"}
        )

        assert response.status_code == 200
        assert response.json()["chat_id"] == chat_id
        broadcast_mock.assert_awaited_once()
        assert data["content"] == "Hello, world!"

    def test_send_message_not_participant(self):
        create_response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["otheruser"]}
        )
        assert create_response.status_code == 201
        chat_id = create_response.json()["id"]

        reg3 = self.client.post(
            "/api/auth/register",
            json={"email": "unauth@example.com", "username": "unauthuser", "password": "123456"}
        )
        if reg3.status_code == 200:
            token3 = reg3.cookies.get("access_token")
        else:
            login3 = self.client.post(
                "/api/auth/login",
                json={"email": "unauth@example.com", "password": "123456"}
            )
            token3 = login3.cookies.get("access_token")

        unauth_client = TestClient(app)
        unauth_client.cookies.set("access_token", token3)

        response = unauth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Auto-add participant test"}
        )
        assert response.status_code == 200

    def test_get_messages(self):
        create_response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["otheruser"]}
        )
        assert create_response.status_code == 201
        chat_id = create_response.json()["id"]

        self.client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Test message"}
        )

        response = self.client.get(f"/api/chats/{chat_id}/messages")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_message_reactions_flow(self, monkeypatch):
        send_personal_mock = AsyncMock()
        monkeypatch.setattr("app.api.v1.chats.manager.send_personal_message", send_personal_mock)

        self.client.cookies.set("access_token", self.access_token)
        create_response = self.client.post("/api/chats/private", json={"username": "otheruser"})
        assert create_response.status_code in (200, 201)
        chat_id = create_response.json()["id"]

        message_response = self.client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "React to me"}
        )
        assert message_response.status_code == 200
        message_id = message_response.json()["id"]

        response = self.client.put(
            f"/api/chats/{chat_id}/messages/{message_id}/reaction",
            json={"emoji": "❤️"}
        )
        assert response.status_code == 200
        assert response.json()["reactions"] == [
            {"emoji": "❤️", "count": 1, "reacted_by_me": True}
        ]

        duplicate_response = self.client.put(
            f"/api/chats/{chat_id}/messages/{message_id}/reaction",
            json={"emoji": "❤️"}
        )
        assert duplicate_response.status_code == 200
        assert duplicate_response.json()["reactions"] == [
            {"emoji": "❤️", "count": 1, "reacted_by_me": True}
        ]

        self.client.cookies.set("access_token", self.other_token)
        response = self.client.put(
            f"/api/chats/{chat_id}/messages/{message_id}/reaction",
            json={"emoji": "❤️"}
        )
        assert response.status_code == 200
        assert response.json()["reactions"] == [
            {"emoji": "❤️", "count": 2, "reacted_by_me": True}
        ]

        self.client.cookies.set("access_token", self.access_token)
        response = self.client.put(
            f"/api/chats/{chat_id}/messages/{message_id}/reaction",
            json={"emoji": "😂"}
        )
        assert response.status_code == 200
        assert response.json()["reactions"] == [
            {"emoji": "❤️", "count": 1, "reacted_by_me": False},
            {"emoji": "😂", "count": 1, "reacted_by_me": True},
        ]

        response = self.client.delete(f"/api/chats/{chat_id}/messages/{message_id}/reaction")
        assert response.status_code == 200
        assert response.json()["reactions"] == [
            {"emoji": "❤️", "count": 1, "reacted_by_me": False}
        ]

        history_response = self.client.get(f"/api/chats/{chat_id}/messages")
        assert history_response.status_code == 200
        saved_message = next(msg for msg in history_response.json() if msg["id"] == message_id)
        assert saved_message["reactions"] == [
            {"emoji": "❤️", "count": 1, "reacted_by_me": False}
        ]
        assert send_personal_mock.await_count >= 8

    def test_message_reaction_rejects_invalid_emoji_and_missing_message(self):
        self.client.cookies.set("access_token", self.access_token)
        create_response = self.client.post("/api/chats/private", json={"username": "otheruser"})
        assert create_response.status_code in (200, 201)
        chat_id = create_response.json()["id"]

        message_response = self.client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "React validation"}
        )
        assert message_response.status_code == 200
        message_id = message_response.json()["id"]

        invalid_response = self.client.put(
            f"/api/chats/{chat_id}/messages/{message_id}/reaction",
            json={"emoji": "💩"}
        )
        assert invalid_response.status_code == 400

        missing_response = self.client.put(
            f"/api/chats/{chat_id}/messages/not-a-message/reaction",
            json={"emoji": "👍"}
        )
        assert missing_response.status_code == 404

    def test_message_reaction_rejects_non_participant(self):
        self.client.cookies.set("access_token", self.access_token)
        create_response = self.client.post("/api/chats/private", json={"username": "otheruser"})
        assert create_response.status_code in (200, 201)
        chat_id = create_response.json()["id"]

        message_response = self.client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Private reaction"}
        )
        assert message_response.status_code == 200
        message_id = message_response.json()["id"]

        outsider = self.client.post(
            "/api/auth/register",
            json={"phone": "+10000001003", "username": "outsider", "password": "123456"}
        )
        assert outsider.status_code == 200
        self.client.cookies.set("access_token", outsider.cookies.get("access_token"))

        response = self.client.put(
            f"/api/chats/{chat_id}/messages/{message_id}/reaction",
            json={"emoji": "🔥"}
        )
        assert response.status_code == 403


class TestChatWebSocket:
    @pytest.fixture(autouse=True)
    def setup(self, client):
        reg1 = client.post(
            "/api/auth/register",
            json={"email": "ws1@example.com", "username": "wsuser1", "password": "123456"}
        )
        if reg1.status_code == 200:
            self.token1 = reg1.cookies.get("access_token")
        else:
            login1 = client.post(
                "/api/auth/login",
                json={"email": "ws1@example.com", "password": "123456"}
            )
            self.token1 = login1.cookies.get("access_token")

        reg2 = client.post(
            "/api/auth/register",
            json={"email": "ws2@example.com", "username": "wsuser2", "password": "123456"}
        )
        if reg2.status_code == 200:
            self.token2 = reg2.cookies.get("access_token")
        else:
            login2 = client.post(
                "/api/auth/login",
                json={"email": "ws2@example.com", "password": "123456"}
            )
            self.token2 = login2.cookies.get("access_token")

        client.cookies.set("access_token", self.token1)
        chat_response = client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["wsuser2"]}
        )
        assert chat_response.status_code == 201
        self.chat_id = chat_response.json()["id"]
        self.client = client

    def test_websocket_connection_valid(self):
        with self.client.websocket_connect(
            f"/api/chats/ws/{self.chat_id}?token={self.token1}"
        ) as websocket:
            assert websocket is not None

    def test_websocket_send_message(self):
        with self.client.websocket_connect(
            f"/api/chats/ws/{self.chat_id}?token={self.token1}"
        ) as ws1:
            with self.client.websocket_connect(
                f"/api/chats/ws/{self.chat_id}?token={self.token2}"
            ) as ws2:
                ws1.send_json({
                    "type": "new_message",
                    "message": {
                        "id": "test-id-123",
                        "sender_id": "test-sender",
                        "sender_name": "wsuser1",
                        "content": "Hello from user1!",
                        "created_at": 1234567890,
                        "chat_id": self.chat_id
                    }
                })
                
                data = ws2.receive_json()
                assert data["type"] == "new_message"
                assert data["message"]["content"] == "Hello from user1!"

    def test_websocket_ping_pong(self):
        with self.client.websocket_connect(
            f"/api/chats/ws/{self.chat_id}?token={self.token1}"
        ) as websocket:
            websocket.send_json({"type": "ping", "request_id": "healthcheck-1"})
            data = websocket.receive_json()
            assert data["type"] == "pong"
            assert data["request_id"] == "healthcheck-1"

    def test_websocket_multiple_messages(self):
        with self.client.websocket_connect(
            f"/api/chats/ws/{self.chat_id}?token={self.token1}"
        ) as ws1:
            with self.client.websocket_connect(
                f"/api/chats/ws/{self.chat_id}?token={self.token2}"
            ) as ws2:
                for i in range(3):
                    ws1.send_json({
                        "type": "new_message",
                        "message": {
                            "id": f"test-id-{i}",
                            "sender_id": "test-sender",
                            "sender_name": "wsuser1",
                            "content": f"Message {i}",
                            "created_at": 1234567890 + i,
                            "chat_id": self.chat_id
                        }
                    })
                    data = ws2.receive_json()
                    assert data["message"]["content"] == f"Message {i}"

    def test_websocket_connection_unauthorized(self):
        with pytest.raises(Exception):
            with self.client.websocket_connect(f"/api/chats/ws/{self.chat_id}?token=invalid"):
                pass

    def test_websocket_connection_invalid_chat_id(self):
        try:
            with self.client.websocket_connect(f"/api/chats/ws/invalid?token={self.token1}"):
                pass
        except Exception as e:
            assert e is not None

class TestMarkAllMessagesAsRead:
    def test_mark_all_messages_as_read_success(self, auth_client, second_user_client, db_session):
        register_response = second_user_client.post(
            "/api/auth/register",
            json={"email": "second@example.com", "username": "seconduser", "password": "123456"}
        )
        if register_response.status_code != 200:
            login_response = second_user_client.post(
                "/api/auth/login",
                json={"email": "second@example.com", "password": "123456"}
            )
            assert login_response.status_code == 200
        
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["seconduser"]}
        )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        chat_info = auth_client.get(f"/api/chats/{chat_id}")
        assert chat_info.status_code == 200
        participants = chat_info.json().get("participants", [])
        assert len(participants) == 2, f"Expected 2 participants, got {len(participants)}"
        
        message_response = second_user_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Test message"}
        )
        assert message_response.status_code == 200
        
        unread_response = auth_client.get(f"/api/chats/{chat_id}/messages/unread/count")
        assert unread_response.json()["count"] == 1
        
        read_response = auth_client.post(f"/api/chats/{chat_id}/messages/read/all")
        assert read_response.status_code == 200
        assert read_response.json()["marked_count"] == 1
        
        unread_response2 = auth_client.get(f"/api/chats/{chat_id}/messages/unread/count")
        assert unread_response2.json()["count"] == 0

    def test_mark_all_messages_as_read_not_participant(self, auth_client, second_user_client):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["seconduser"]}
        )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        register_response = auth_client.post(
            "/api/auth/register",
            json={"email": "third@example.com", "username": "thirduser", "password": "123456"}
        )
        third_client = TestClient(app)
        if register_response.status_code == 200:
            token = register_response.cookies.get("access_token")
        else:
            login_response = auth_client.post(
                "/api/auth/login",
                json={"email": "third@example.com", "password": "123456"}
            )
            token = login_response.cookies.get("access_token")
        third_client.cookies.set("access_token", token)
        
        response = third_client.post(f"/api/chats/{chat_id}/messages/read/all")
        assert response.status_code == 403
        assert "Not a participant" in response.text

    def test_mark_all_messages_as_read_empty_chat(self, auth_client, second_user_client):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["seconduser"]}
        )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        response = auth_client.post(f"/api/chats/{chat_id}/messages/read/all")
        assert response.status_code == 200
        assert response.json()["marked_count"] == 0

    def test_mark_all_messages_as_read_invalid_chat(self, auth_client):
        response = auth_client.post("/api/chats/invalid-id/messages/read/all")
        assert response.status_code == 404
        assert "Chat not found" in response.text or "Invalid chat ID" in response.text

class TestImageMessages:
    def _create_chat(self, auth_client):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": True, "name": "Test Group", "participant_ids": []}
        )
        if chat_response.status_code != 201:
            chat_response = auth_client.post(
                "/api/chats/",
                json={"is_group": False, "participant_ids": [auth_client.user_id]}
            )
        assert chat_response.status_code == 201
        return chat_response.json()["id"]

    def test_send_image_message_success(self, auth_client):
        chat_id = self._create_chat(auth_client)
        
        response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "/uploads/images/test.jpg", "is_image": True}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_image"] is True
        assert data["content"] == "/uploads/images/test.jpg"

    def test_send_image_message_without_text_success(self, auth_client):
        chat_id = self._create_chat(auth_client)

        response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"images": ["/uploads/images/one.jpg"], "is_image": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["content"] == ""
        assert data["is_image"] is True
        assert data["images"] == ["/uploads/images/one.jpg"]

    def test_send_text_and_images_as_single_message(self, auth_client):
        chat_id = self._create_chat(auth_client)

        response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={
                "content": "Смотри какие фотографии",
                "images": ["/uploads/images/one.jpg", "/uploads/images/two.jpg"],
                "is_image": True
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["content"] == "Смотри какие фотографии"
        assert data["is_image"] is True
        assert data["images"] == ["/uploads/images/one.jpg", "/uploads/images/two.jpg"]

        history_response = auth_client.get(f"/api/chats/{chat_id}/messages")
        assert history_response.status_code == 200
        matching = [msg for msg in history_response.json() if msg["id"] == data["id"]]
        assert len(matching) == 1
        assert matching[0]["content"] == "Смотри какие фотографии"
        assert matching[0]["images"] == ["/uploads/images/one.jpg", "/uploads/images/two.jpg"]

    def test_send_reply_text_and_image_keeps_reply_to(self, auth_client):
        chat_id = self._create_chat(auth_client)

        original_response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Original message"}
        )
        assert original_response.status_code == 200
        original_id = original_response.json()["id"]

        reply_response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={
                "content": "Reply with photo",
                "images": ["/uploads/images/reply.jpg"],
                "reply_to_id": original_id
            }
        )

        assert reply_response.status_code == 200
        data = reply_response.json()
        assert data["reply_to_id"] == original_id
        assert data["content"] == "Reply with photo"
        assert data["images"] == ["/uploads/images/reply.jpg"]

    def test_send_empty_message_rejected(self, auth_client):
        chat_id = self._create_chat(auth_client)

        response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": ""}
        )

        assert response.status_code == 422
    
    def test_send_regular_message_without_is_image(self, auth_client):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": True, "name": "Test Group 2", "participant_ids": []}
        )
        if chat_response.status_code != 201:
            chat_response = auth_client.post(
                "/api/chats/",
                json={"is_group": False, "participant_ids": [auth_client.user_id]}
            )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Hello, world!"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_image"] is False
        assert data["content"] == "Hello, world!"
    
    def test_get_message_with_image_returns_is_image_true(self, auth_client):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": True, "name": "Test Group 3", "participant_ids": []}
        )
        if chat_response.status_code != 201:
            chat_response = auth_client.post(
                "/api/chats/",
                json={"is_group": False, "participant_ids": [auth_client.user_id]}
            )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        send_response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "/uploads/images/photo.png", "is_image": True}
        )
        assert send_response.status_code == 200
        
        get_response = auth_client.get(f"/api/chats/{chat_id}/messages")
        assert get_response.status_code == 200
        messages = get_response.json()
        
        found = False
        for msg in messages:
            if msg["content"] == "/uploads/images/photo.png":
                assert msg["is_image"] is True
                found = True
                break
        assert found is True

class TestReplyToMessage:
    def test_send_reply_to_message_success(self, auth_client, db_session):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": True, "name": "Test Group", "participant_ids": []}
        )
        if chat_response.status_code != 201:
            chat_response = auth_client.post(
                "/api/chats/",
                json={"is_group": False, "participant_ids": [auth_client.user_id]}
            )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        msg1_response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Original message"}
        )
        assert msg1_response.status_code == 200
        msg1_id = msg1_response.json()["id"]
        
        reply_response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Reply message", "reply_to_id": msg1_id}
        )
        assert reply_response.status_code == 200
        data = reply_response.json()
        
        assert data["reply_to_id"] == msg1_id
        assert data["content"] == "Reply message"
    
    def test_send_reply_to_nonexistent_message(self, auth_client, db_session):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": True, "name": "Test Group 2", "participant_ids": []}
        )
        if chat_response.status_code != 201:
            chat_response = auth_client.post(
                "/api/chats/",
                json={"is_group": False, "participant_ids": [auth_client.user_id]}
            )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Reply", "reply_to_id": "nonexistent-id"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reply_to_id"] == "nonexistent-id"
    
    def test_get_message_with_reply_to(self, auth_client, db_session):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": True, "name": "Test Group 3", "participant_ids": []}
        )
        if chat_response.status_code != 201:
            chat_response = auth_client.post(
                "/api/chats/",
                json={"is_group": False, "participant_ids": [auth_client.user_id]}
            )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        msg1 = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Parent message"}
        ).json()
        
        msg2 = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Child message", "reply_to_id": msg1["id"]}
        ).json()
        
        messages_response = auth_client.get(f"/api/chats/{chat_id}/messages")
        assert messages_response.status_code == 200
        messages = messages_response.json()
        
        found = False
        for msg in messages:
            if msg["id"] == msg2["id"]:
                assert msg["reply_to_id"] == msg1["id"]
                found = True
                break
        assert found is True


class TestMessageForwarding:
    @pytest.fixture(autouse=True)
    def setup(self, client, monkeypatch):
        from app.api.v1 import chats

        self.client = client
        self.broadcast_mock = AsyncMock()
        monkeypatch.setattr(chats.manager, "broadcast_to_chat", self.broadcast_mock)
        monkeypatch.setattr(chats, "send_fcm_notification", AsyncMock())

        users = [
            ("+10000002001", "forward_a"),
            ("+10000002002", "forward_b"),
            ("+10000002003", "forward_c"),
            ("+10000002004", "forward_d"),
        ]
        self.tokens = {}
        self.user_ids = {}
        for phone, username in users:
            response = client.post(
                "/api/auth/register",
                json={"phone": phone, "username": username, "password": "123456"}
            )
            if response.status_code != 200:
                response = client.post(
                    "/api/auth/login",
                    json={"phone": phone, "password": "123456"}
                )
            data = response.json()
            self.tokens[username] = response.cookies.get("access_token")
            self.user_ids[username] = data["user"]["id"]

    def as_user(self, username: str):
        self.client.cookies.set("access_token", self.tokens[username])

    def create_private_chat(self, owner_username: str, other_username: str) -> str:
        self.as_user(owner_username)
        response = self.client.post("/api/chats/private", json={"username": other_username})
        assert response.status_code in (200, 201), response.text
        return response.json()["id"]

    def send_message(self, username: str, chat_id: str, payload: dict) -> dict:
        self.as_user(username)
        response = self.client.post(f"/api/chats/{chat_id}/messages", json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    def forward_message(self, username: str, source_chat_id: str, message_id: str, target_chat_id: str):
        self.as_user(username)
        return self.client.post(
            f"/api/chats/{source_chat_id}/messages/{message_id}/forward",
            json={"target_chat_id": target_chat_id}
        )

    def test_forward_text_message_to_another_chat_history_realtime_unread_last_message(self):
        ab_chat_id = self.create_private_chat("forward_a", "forward_b")
        bc_chat_id = self.create_private_chat("forward_b", "forward_c")
        source = self.send_message("forward_a", ab_chat_id, {"content": "hello for C"})

        response = self.forward_message("forward_b", ab_chat_id, source["id"], bc_chat_id)

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["id"] != source["id"]
        assert data["chat_id"] == bc_chat_id
        assert data["sender_id"] == self.user_ids["forward_b"]
        assert data["content"] == "hello for C"
        assert data["forwarded_from_message_id"] == source["id"]
        assert data["forwarded_from_user_id"] == self.user_ids["forward_a"]
        assert data["forwarded_from_user_name"] == "forward_a"
        assert data["reply_to_id"] is None
        assert data["reactions"] == []

        ws_payload = self.broadcast_mock.await_args_list[-1].kwargs
        assert ws_payload["chat_id"] == bc_chat_id
        assert ws_payload["exclude_user_id"] == self.user_ids["forward_b"]
        ws_message = self.broadcast_mock.await_args_list[-1].args[0]["message"]
        assert ws_message["id"] == data["id"]
        assert ws_message["forwarded_from_user_name"] == "forward_a"
        assert ws_message["reactions"] == []

        self.as_user("forward_c")
        history = self.client.get(f"/api/chats/{bc_chat_id}/messages")
        assert history.status_code == 200
        saved = next(message for message in history.json() if message["id"] == data["id"])
        assert saved["forwarded_from_user_name"] == "forward_a"
        assert saved["content"] == "hello for C"

        unread = self.client.get(f"/api/chats/{bc_chat_id}/messages/unread/count")
        assert unread.status_code == 200
        assert unread.json()["count"] == 1

        last_message = self.client.get(f"/api/chats/{bc_chat_id}/last-message")
        assert last_message.status_code == 200
        assert last_message.json()["id"] == data["id"]
        assert last_message.json()["content"] == "hello for C"

        self.as_user("forward_b")
        source_history = self.client.get(f"/api/chats/{ab_chat_id}/messages")
        saved_source = next(message for message in source_history.json() if message["id"] == source["id"])
        assert saved_source["forwarded_from_message_id"] is None
        assert saved_source["reactions"] == []

    @pytest.mark.parametrize(
        "payload, expected_content, expected_images",
        [
            ({"content": "", "is_image": True, "images": ["/uploads/images/one.png"]}, "", ["/uploads/images/one.png"]),
            ({"content": "", "is_image": True, "images": ["/uploads/images/one.png", "/uploads/images/two.png"]}, "", ["/uploads/images/one.png", "/uploads/images/two.png"]),
            ({"content": "caption", "is_image": True, "images": ["/uploads/images/one.png"]}, "caption", ["/uploads/images/one.png"]),
        ],
    )
    def test_forward_images_and_text_images(self, payload, expected_content, expected_images):
        ab_chat_id = self.create_private_chat("forward_a", "forward_b")
        bc_chat_id = self.create_private_chat("forward_b", "forward_c")
        source = self.send_message("forward_a", ab_chat_id, payload)

        response = self.forward_message("forward_b", ab_chat_id, source["id"], bc_chat_id)

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["content"] == expected_content
        assert data["is_image"] is True
        assert data["images"] == expected_images
        assert data["forwarded_from_user_name"] == "forward_a"
        assert data["reactions"] == []

    def test_forward_reply_does_not_copy_reply_to_and_can_be_reacted_and_replied_to(self):
        ab_chat_id = self.create_private_chat("forward_a", "forward_b")
        bc_chat_id = self.create_private_chat("forward_b", "forward_c")
        parent = self.send_message("forward_a", ab_chat_id, {"content": "parent"})
        reply = self.send_message(
            "forward_a",
            ab_chat_id,
            {"content": "reply body", "reply_to_id": parent["id"]}
        )

        response = self.forward_message("forward_b", ab_chat_id, reply["id"], bc_chat_id)

        assert response.status_code == 200, response.text
        forwarded = response.json()
        assert forwarded["content"] == "reply body"
        assert forwarded["reply_to_id"] is None
        assert forwarded["forwarded_from_message_id"] == reply["id"]

        self.as_user("forward_c")
        reaction = self.client.put(
            f"/api/chats/{bc_chat_id}/messages/{forwarded['id']}/reaction",
            json={"emoji": "👍"}
        )
        assert reaction.status_code == 200
        assert reaction.json()["reactions"] == [{"emoji": "👍", "count": 1, "reacted_by_me": True}]

        reply_to_forward = self.client.post(
            f"/api/chats/{bc_chat_id}/messages",
            json={"content": "reply to forwarded", "reply_to_id": forwarded["id"]}
        )
        assert reply_to_forward.status_code == 200
        assert reply_to_forward.json()["reply_to_id"] == forwarded["id"]

    def test_forwarding_forwarded_message_keeps_original_author_metadata(self):
        ab_chat_id = self.create_private_chat("forward_a", "forward_b")
        bc_chat_id = self.create_private_chat("forward_b", "forward_c")
        ac_chat_id = self.create_private_chat("forward_a", "forward_c")
        source = self.send_message("forward_a", ab_chat_id, {"content": "chain"})

        first_forward = self.forward_message("forward_b", ab_chat_id, source["id"], bc_chat_id)
        assert first_forward.status_code == 200, first_forward.text
        second_forward = self.forward_message("forward_c", bc_chat_id, first_forward.json()["id"], ac_chat_id)

        assert second_forward.status_code == 200, second_forward.text
        data = second_forward.json()
        assert data["content"] == "chain"
        assert data["forwarded_from_message_id"] == source["id"]
        assert data["forwarded_from_user_id"] == self.user_ids["forward_a"]
        assert data["forwarded_from_user_name"] == "forward_a"

    def test_forward_rejects_inaccessible_source_and_target_chats(self):
        ab_chat_id = self.create_private_chat("forward_a", "forward_b")
        ac_chat_id = self.create_private_chat("forward_a", "forward_c")
        source = self.send_message("forward_a", ab_chat_id, {"content": "private"})

        inaccessible_source = self.forward_message("forward_d", ab_chat_id, source["id"], ac_chat_id)
        assert inaccessible_source.status_code == 403

        inaccessible_target = self.forward_message("forward_b", ab_chat_id, source["id"], ac_chat_id)
        assert inaccessible_target.status_code == 403

        self.as_user("forward_c")
        history = self.client.get(f"/api/chats/{ac_chat_id}/messages")
        assert history.status_code == 200
        assert all(message["content"] != "private" for message in history.json())


class TestMessageEditingAndDeletion:
    @pytest.fixture(autouse=True)
    def setup(self, client, monkeypatch):
        from app.api.v1 import chats

        self.client = client
        self.broadcast_mock = AsyncMock()
        monkeypatch.setattr(chats.manager, "broadcast_to_chat", self.broadcast_mock)
        monkeypatch.setattr(chats, "send_fcm_notification", AsyncMock())

        users = [
            ("+10000003001", "edit_a"),
            ("+10000003002", "edit_b"),
        ]
        self.tokens = {}
        self.user_ids = {}
        for phone, username in users:
            response = client.post(
                "/api/auth/register",
                json={"phone": phone, "username": username, "password": "123456"}
            )
            if response.status_code != 200:
                response = client.post(
                    "/api/auth/login",
                    json={"phone": phone, "password": "123456"}
                )
            data = response.json()
            self.tokens[username] = response.cookies.get("access_token")
            self.user_ids[username] = data["user"]["id"]

    def as_user(self, username: str):
        self.client.cookies.set("access_token", self.tokens[username])

    def create_private_chat(self, owner_username: str, other_username: str) -> str:
        self.as_user(owner_username)
        response = self.client.post("/api/chats/private", json={"username": other_username})
        assert response.status_code in (200, 201), response.text
        return response.json()["id"]

    def send_message(self, username: str, chat_id: str, payload: dict) -> dict:
        self.as_user(username)
        response = self.client.post(f"/api/chats/{chat_id}/messages", json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    def test_edit_message_updates_history_and_realtime_payload(self):
        chat_id = self.create_private_chat("edit_a", "edit_b")
        source = self.send_message("edit_a", chat_id, {"content": "draft"})

        self.broadcast_mock.reset_mock()
        self.as_user("edit_a")
        response = self.client.patch(
            f"/api/chats/{chat_id}/messages/{source['id']}",
            json={"content": "final text"}
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["content"] == "final text"
        assert data["edited_at"] is not None
        assert data["deleted_at"] is None

        ws_payload = self.broadcast_mock.await_args_list[-1].args[0]
        assert ws_payload["type"] == "edit_message"
        assert ws_payload["message"]["id"] == source["id"]
        assert ws_payload["message"]["content"] == "final text"
        assert ws_payload["message"]["edited_at"] == data["edited_at"]

        self.as_user("edit_b")
        history = self.client.get(f"/api/chats/{chat_id}/messages")
        assert history.status_code == 200
        saved = next(message for message in history.json() if message["id"] == source["id"])
        assert saved["content"] == "final text"
        assert saved["edited_at"] == data["edited_at"]

    def test_delete_message_marks_deleted_and_last_message_preview_changes(self):
        chat_id = self.create_private_chat("edit_a", "edit_b")
        source = self.send_message("edit_a", chat_id, {"content": "delete me"})

        self.broadcast_mock.reset_mock()
        self.as_user("edit_a")
        response = self.client.delete(f"/api/chats/{chat_id}/messages/{source['id']}")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok"
        assert response.json()["deleted_at"] is not None

        ws_payload = self.broadcast_mock.await_args_list[-1].args[0]
        assert ws_payload["type"] == "delete_message"
        assert ws_payload["message"]["id"] == source["id"]
        assert ws_payload["message"]["deleted_at"] == response.json()["deleted_at"]

        self.as_user("edit_b")
        history = self.client.get(f"/api/chats/{chat_id}/messages")
        assert history.status_code == 200
        saved = next(message for message in history.json() if message["id"] == source["id"])
        assert saved["deleted_at"] == response.json()["deleted_at"]
        assert saved["content"] == "delete me"

        last_message = self.client.get(f"/api/chats/{chat_id}/last-message")
        assert last_message.status_code == 200
        assert last_message.json()["content"] == "Message deleted"

    def test_edit_and_delete_reject_other_users_and_image_edits(self):
        chat_id = self.create_private_chat("edit_a", "edit_b")
        source = self.send_message("edit_a", chat_id, {"content": "private"})
        image_message = self.send_message(
            "edit_a",
            chat_id,
            {"content": "caption", "is_image": True, "images": ["/uploads/images/one.png"]}
        )

        self.as_user("edit_b")
        forbidden_edit = self.client.patch(
            f"/api/chats/{chat_id}/messages/{source['id']}",
            json={"content": "hack"}
        )
        assert forbidden_edit.status_code == 403

        forbidden_delete = self.client.delete(f"/api/chats/{chat_id}/messages/{source['id']}")
        assert forbidden_delete.status_code == 403

        self.as_user("edit_a")
        image_edit = self.client.patch(
            f"/api/chats/{chat_id}/messages/{image_message['id']}",
            json={"content": "new caption"}
        )
        assert image_edit.status_code == 400


class TestChatParticipantsAvatar:
    def test_chat_participants_have_avatar_field(self, auth_client):
        response = auth_client.get("/api/chats/")
        assert response.status_code == 200
        chats = response.json()
        
        if chats:
            for chat in chats:
                for participant in chat.get("participants", []):
                    assert "avatar" in participant
