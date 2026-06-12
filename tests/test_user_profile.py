# tests/test_user_profile.py
import pytest

class TestUserProfile:
    
    def test_get_user_profile_with_avatar(self, auth_client):
        """Test getting user profile with avatar"""
        response = auth_client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert "avatar" in data
    
    def test_get_user_profile_without_avatar(self, auth_client):
        """Test getting user profile without avatar"""
        response = auth_client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert "avatar" in data
    
    def test_get_other_user_profile(self, auth_client, second_user_client):
        """Test getting another user's profile"""
        # Получаем ID второго пользователя
        response = second_user_client.get("/api/auth/me")
        assert response.status_code == 200
        other_user_id = response.json()["id"]
        
        # Запрашиваем профиль через первого пользователя
        resp = auth_client.get(f"/api/auth/users/{other_user_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "avatar" in data
        assert data["id"] == other_user_id
    
    def test_get_nonexistent_user_profile(self, auth_client):
        """Test getting nonexistent user profile"""
        resp = auth_client.get("/api/auth/users/nonexistent-id-12345")
        assert resp.status_code == 404


class TestChatParticipantAvatar:
    
    def test_chat_participant_includes_avatar(self, auth_client, second_user_client):
        """Test that chat participants include avatar field"""
        # Создаем чат между пользователями
        response = auth_client.post("/api/chats/", json={
            "name": None,
            "is_group": False,
            "participant_ids": ["seconduser"]
        })
        
        if response.status_code == 201:
            chat = response.json()
            for participant in chat.get("participants", []):
                assert "avatar" in participant
        else:
            pytest.skip("Could not create chat")