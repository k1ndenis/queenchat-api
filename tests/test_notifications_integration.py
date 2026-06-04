import pytest
from main import app

class TestNotificationsIntegration:
    
    def test_full_notification_flow(self, auth_client, db_session):
        register_response = auth_client.post(
            "/api/auth/register",
            json={"email": "seconduser@example.com", "username": "seconduser", "password": "123456"}
        )
        assert register_response.status_code == 200
        
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": False, "participant_ids": ["seconduser"]}
        )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        message_response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": "Test notification message"}
        )
        assert message_response.status_code == 200
        
        notif_response = auth_client.get("/api/notifications/")
        assert notif_response.status_code == 200
        
        unread_response = auth_client.get("/api/notifications/unread/count")
        assert unread_response.status_code == 200
        
        notifications = notif_response.json()
        if notifications:
            read_response = auth_client.patch(f"/api/notifications/{notifications[0]['id']}/read")
            assert read_response.status_code == 200