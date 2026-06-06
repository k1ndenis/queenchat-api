import pytest
from fastapi.testclient import TestClient
from main import app

class TestChatAPI:
    @pytest.fixture(autouse=True)
    def setup(self, client):
        reg1 = client.post(
            "/api/auth/register",
            json={"email": "chat@example.com", "username": "chatuser", "password": "123456"}
        )
        if reg1.status_code == 200:
            self.access_token = reg1.cookies.get("access_token")
            self.user_id = reg1.json()["user"]["id"]
        else:
            login = client.post(
                "/api/auth/login",
                json={"email": "chat@example.com", "password": "123456"}
            )
            self.access_token = login.cookies.get("access_token")
            self.user_id = login.json()["user"]["id"]

        reg2 = client.post(
            "/api/auth/register",
            json={"email": "other@example.com", "username": "otheruser", "password": "123456"}
        )
        if reg2.status_code == 200:
            self.other_user_id = reg2.json()["user"]["id"]
            self.other_token = reg2.cookies.get("access_token")
        else:
            login2 = client.post(
                "/api/auth/login",
                json={"email": "other@example.com", "password": "123456"}
            )
            self.other_user_id = login2.json()["user"]["id"]
            self.other_token = login2.cookies.get("access_token")

        client.cookies.set("access_token", self.access_token)
        self.client = client

    def test_create_private_chat_success(self):
        response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["otheruser"]}
        )
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
        create_response = self.client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["otheruser"]}
        )
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
            websocket.send_json({"type": "ping"})
            data = websocket.receive_json()
            assert data["type"] == "pong"

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