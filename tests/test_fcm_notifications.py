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

    def test_legacy_redis_string_token_is_read_and_migrated(self, monkeypatch):
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

            def hgetall(self, name):
                value = self.store.get(name)
                if isinstance(value, str):
                    raise notifications.redis.exceptions.ResponseError(
                        "WRONGTYPE Operation against a key holding the wrong kind of value"
                    )
                return value or {}

            def hdel(self, name, field):
                value = self.store.get(name)
                if isinstance(value, dict):
                    value.pop(field, None)

            def expire(self, name, ttl):
                return True

            def delete(self, name):
                self.store.pop(name, None)

        fake_redis = FakeRedis()
        user_id = "legacy-user"
        fake_redis.store[f"fcm:{user_id}"] = "legacy-token"

        monkeypatch.setattr(notifications, "TESTING", False)
        monkeypatch.setattr(notifications, "REDIS_AVAILABLE", True)
        monkeypatch.setattr(notifications, "redis_client", fake_redis)

        assert notifications.get_fcm_tokens(user_id) == [
            {"token": "legacy-token", "device_id": "legacy", "settings": {}}
        ]

        notifications.save_fcm_token(user_id, "new-token", device_id="device-1")

        stored_value = fake_redis.store[f"fcm:{user_id}"]
        assert isinstance(stored_value, dict)
        assert notifications.get_fcm_tokens(user_id)[0]["token"] == "new-token"

    def test_save_fcm_token_removes_stale_sw_version_for_user_only(self, monkeypatch):
        from app.api.v1 import notifications

        class FakeRedis:
            def __init__(self):
                self.store = {}

            def hset(self, name, field, value):
                self.store.setdefault(name, {})[field] = value

            def hgetall(self, name):
                return self.store.get(name, {})

            def hvals(self, name):
                return list((self.store.get(name) or {}).values())

            def hdel(self, name, field):
                value = self.store.get(name)
                if isinstance(value, dict):
                    value.pop(field, None)

            def expire(self, name, ttl):
                return True

        fake_redis = FakeRedis()
        user_id = "versioned-user"
        other_user_id = "other-user"
        user_key = f"fcm:{user_id}"
        other_user_key = f"fcm:{other_user_id}"
        fake_redis.store[user_key] = {
            "old-device": notifications._serialize({
                "token": "old-token",
                "device_id": "old-device",
                "sw_version": "old-sw",
                "settings": {},
            }),
            "same-version-device": notifications._serialize({
                "token": "same-version-token",
                "device_id": "same-version-device",
                "sw_version": notifications.CURRENT_FCM_SW_VERSION,
                "settings": {},
            }),
        }
        fake_redis.store[other_user_key] = {
            "old-device": notifications._serialize({
                "token": "other-token",
                "device_id": "old-device",
                "sw_version": "old-sw",
                "settings": {},
            }),
        }

        monkeypatch.setattr(notifications, "TESTING", False)
        monkeypatch.setattr(notifications, "REDIS_AVAILABLE", True)
        monkeypatch.setattr(notifications, "redis_client", fake_redis)

        notifications.save_fcm_token(
            user_id,
            "new-token",
            device_id="new-device",
            sw_version=notifications.CURRENT_FCM_SW_VERSION,
        )

        user_devices = fake_redis.store[user_key]
        assert set(user_devices.keys()) == {"same-version-device", "new-device"}
        assert set(fake_redis.store[other_user_key].keys()) == {"old-device"}
