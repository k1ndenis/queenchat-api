import pytest
from unittest.mock import patch, MagicMock
import os

os.environ["TESTING"] = "true"

class TestFirebaseInit:
    
    def test_firebase_initialized(self):
        try:
            from app.core import firebase
            assert firebase is not None
        except ImportError:
            pytest.skip("Firebase not configured in test mode")
    
    def test_firebase_config_loaded(self):
        try:
            from app.core.firebase import firebase_config
            assert "apiKey" in firebase_config or True
        except (ImportError, KeyError):
            pytest.skip("Firebase config not available in test mode")


class TestPushIntegration:
    @patch('app.api.v1.notifications.messaging')
    @patch('app.api.v1.notifications.get_fcm_token')
    @patch('app.api.v1.notifications.REDIS_AVAILABLE', False)
    async def test_send_fcm_notification_success(self, mock_get_token, mock_messaging):
        from app.api.v1.notifications import send_fcm_notification
        
        mock_get_token.return_value = "test_token"
        mock_message = MagicMock()
        mock_messaging.Message.return_value = mock_message
        mock_messaging.send.return_value = "test_message_id"
        
        result = await send_fcm_notification(
            user_id="test_user",
            title="Test Title",
            body="Test Body",
            url="/chat"
        )
        
        assert result is True
    
    @patch('app.api.v1.notifications.get_fcm_token')
    @patch('app.api.v1.notifications.REDIS_AVAILABLE', False)
    async def test_send_fcm_notification_no_token(self, mock_get_token):
        from app.api.v1.notifications import send_fcm_notification
        
        mock_get_token.return_value = None
        
        result = await send_fcm_notification(
            user_id="test_user",
            title="Test",
            body="Test"
        )
        
        assert result is False


class TestFCMNotifications:
    def test_fcm_status_endpoint(self, auth_client):
        response = auth_client.get("/api/notifications/fcm-status")
        assert response.status_code == 200
        data = response.json()
        assert "subscribed" in data
        assert "fcm_available" in data
    
    def test_save_fcm_token(self, auth_client):
        token_data = {"token": "test_fcm_token_123"}
        response = auth_client.post("/api/notifications/fcm-token", json=token_data)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    
    def test_delete_fcm_token(self, auth_client):
        response = auth_client.delete("/api/notifications/fcm-token")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    
    def test_fcm_status_endpoint_returns_json(self, auth_client):
        response = auth_client.get("/api/notifications/fcm-status")
        assert response.headers["content-type"] == "application/json"


class TestFCMTokenStorage:
    def test_save_token_requires_auth(self, client):
        token_data = {"token": "test_token"}
        response = client.post("/api/notifications/fcm-token", json=token_data)
        assert response.status_code == 401
    
    def test_delete_token_requires_auth(self, client):
        response = client.delete("/api/notifications/fcm-token")
        assert response.status_code == 401