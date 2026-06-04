import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

class TestAuthAPI:
    def test_register_success(self, client):
        response = client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "username": "testuser", "password": "123456"}
        )
        assert response.status_code == 200
        assert "user" in response.json()
        assert response.json()["user"]["username"] == "testuser"
        assert "access_token" in response.cookies

    def test_register_duplicate_email(self, client):
        reg1 = client.post(
            "/api/auth/register",
            json={"email": "existing@example.com", "username": "user1", "password": "123456"}
        )
        assert reg1.status_code == 200

        response = client.post(
            "/api/auth/register",
            json={"email": "existing@example.com", "username": "user2", "password": "123456"}
        )
        assert response.status_code == 400
        assert "Email already registered" in response.text

    def test_register_duplicate_username(self, client):
        reg1 = client.post(
            "/api/auth/register",
            json={"email": "email1@example.com", "username": "sameuser", "password": "123456"}
        )
        assert reg1.status_code == 200

        response = client.post(
            "/api/auth/register",
            json={"email": "email2@example.com", "username": "sameuser", "password": "123456"}
        )
        assert response.status_code == 400
        assert "username already taken" in response.text.lower()

    def test_register_invalid_email(self, client):
        response = client.post(
            "/api/auth/register",
            json={"email": "invalid-email", "username": "testuser", "password": "123456"}
        )
        assert response.status_code == 422

    def test_login_success(self, client):
        reg = client.post(
            "/api/auth/register",
            json={"email": "login@example.com", "username": "loginuser", "password": "123456"}
        )
        assert reg.status_code == 200

        response = client.post(
            "/api/auth/login",
            json={"email": "login@example.com", "password": "123456"}
        )
        assert response.status_code == 200
        assert "user" in response.json()
        assert "access_token" in response.cookies

    def test_login_invalid_credentials(self, client):
        response = client.post(
            "/api/auth/login",
            json={"email": "wrong@example.com", "password": "wrong"}
        )
        assert response.status_code == 401
        assert "Invalid email or password" in response.text

    def test_get_me_authenticated(self, client):
        register_response = client.post(
            "/api/auth/register",
            json={"email": "me@example.com", "username": "meuser", "password": "123456"}
        )
        assert register_response.status_code == 200

        client.cookies.set("access_token", register_response.cookies["access_token"])
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        assert response.json()["username"] == "meuser"
        client.cookies.clear()

    def test_get_me_unauthenticated(self, client):
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    def test_logout(self, client):
        register_response = client.post(
            "/api/auth/register",
            json={"email": "logout@example.com", "username": "logoutuser", "password": "123456"}
        )
        assert register_response.status_code == 200

        client.cookies.set("access_token", register_response.cookies["access_token"])
        response = client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.json()["message"] == "Logged out successfully"
        client.cookies.clear()

    def test_register_missing_fields(self, client):
        response = client.post(
            "/api/auth/register",
            json={"email": "test@example.com"}
        )
        assert response.status_code == 422

    def test_get_ws_token_authenticated(self, client):
        register_response = client.post(
            "/api/auth/register",
            json={"email": "ws@example.com", "username": "wsuser", "password": "123456"}
        )
        assert register_response.status_code == 200

        client.cookies.set("access_token", register_response.cookies["access_token"])
        response = client.get("/api/auth/ws-token")
        assert response.status_code == 200
        assert "token" in response.json()
        client.cookies.clear()


class TestAuthIntegration:
    def test_full_auth_flow(self, client):
        register_response = client.post(
            "/api/auth/register",
            json={"email": "flow@example.com", "username": "flowuser", "password": "123456"}
        )
        assert register_response.status_code == 200

        client.cookies.set("access_token", register_response.cookies["access_token"])

        me_response = client.get("/api/auth/me")
        assert me_response.status_code == 200
        assert me_response.json()["username"] == "flowuser"

        logout_response = client.post("/api/auth/logout")
        assert logout_response.status_code == 200
        client.cookies.clear()

    def test_cannot_register_same_email_twice(self, client):
        response1 = client.post(
            "/api/auth/register",
            json={"email": "unique@example.com", "username": "user1", "password": "123456"}
        )
        assert response1.status_code == 200

        response2 = client.post(
            "/api/auth/register",
            json={"email": "unique@example.com", "username": "user2", "password": "123456"}
        )
        assert response2.status_code == 400
        assert "Email already registered" in response2.text

    def test_cannot_register_same_username_twice(self, client):
        response1 = client.post(
            "/api/auth/register",
            json={"email": "unique-email@example.com", "username": "uniqueusername", "password": "123456"}
        )
        assert response1.status_code == 200

        response2 = client.post(
            "/api/auth/register",
            json={"email": "unique-email2@example.com", "username": "uniqueusername", "password": "123456"}
        )
        assert response2.status_code == 400
        assert "username already taken" in response2.text.lower()