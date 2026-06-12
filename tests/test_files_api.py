import pytest
import os
import json

class TestImageGallery:
    def test_send_multiple_images(self, auth_client):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": True, "name": "Test Group", "participant_ids": []}
        )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        images = [
            "/uploads/images/img1.png",
            "/uploads/images/img2.png",
            "/uploads/images/img3.png"
        ]
        
        print(f"📸 Sending images: {images}")
        
        response = auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": json.dumps(images), "is_image": True, "images": images}
        )
        
        print(f"📸 Response: {response.json()}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["is_image"] is True
        assert data["content"] == json.dumps(images)
    
    def test_get_messages_with_images(self, auth_client):
        chat_response = auth_client.post(
            "/api/chats/",
            json={"is_group": True, "name": "Test Group 2", "participant_ids": []}
        )
        assert chat_response.status_code == 201
        chat_id = chat_response.json()["id"]
        
        images = ["/uploads/images/test1.png", "/uploads/images/test2.png"]
        
        auth_client.post(
            f"/api/chats/{chat_id}/messages",
            json={"content": json.dumps(images), "is_image": True, "images": images}
        )
        
        messages_response = auth_client.get(f"/api/chats/{chat_id}/messages")
        assert messages_response.status_code == 200
        messages = messages_response.json()
        
        found = False
        for msg in messages:
            if msg.get("content") == json.dumps(images):
                found = True
                break
        assert found is True

class TestAvatarUpload:
    def test_upload_avatar_success(self, auth_client):
        """Тест: успешная загрузка аватара"""
        test_file = "/tmp/test_avatar.jpg"
        with open(test_file, "wb") as f:
            f.write(b'test image data')
        
        with open(test_file, "rb") as f:
            response = auth_client.post(
                "/api/files/upload-avatar",
                files={"file": ("avatar.jpg", f, "image/jpeg")}
            )
        
        os.remove(test_file)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "url" in data
        assert data["url"].startswith("/uploads/images/avatar_")
    
    def test_upload_avatar_invalid_type(self, auth_client):
        test_file = "/tmp/test.txt"
        with open(test_file, "wb") as f:
            f.write(b'not an image')
        
        with open(test_file, "rb") as f:
            response = auth_client.post(
                "/api/files/upload-avatar",
                files={"file": ("test.txt", f, "text/plain")}
            )
        
        os.remove(test_file)
        
        assert response.status_code == 400
        assert "File must be an image" in response.json()["detail"]
    
    def test_upload_avatar_too_large(self, auth_client):
        large_data = b'x' * (3 * 1024 * 1024)
        test_file = "/tmp/large.jpg"
        with open(test_file, "wb") as f:
            f.write(large_data)
        
        with open(test_file, "rb") as f:
            response = auth_client.post(
                "/api/files/upload-avatar",
                files={"file": ("large.jpg", f, "image/jpeg")}
            )
        
        os.remove(test_file)
        
        assert response.status_code == 400
        assert "too large" in response.json()["detail"]
    
    def test_upload_avatar_unauthorized(self, client):
        test_file = "/tmp/test.jpg"
        with open(test_file, "wb") as f:
            f.write(b'test')
        
        with open(test_file, "rb") as f:
            response = client.post(
                "/api/files/upload-avatar",
                files={"file": ("test.jpg", f, "image/jpeg")}
            )
        
        os.remove(test_file)
        
        assert response.status_code == 401