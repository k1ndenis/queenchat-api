import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from main import app
from app.core.database import Base
from app.core.dependency import get_db

DATABASE_URL = "sqlite:///./test.db"

@pytest.fixture(scope="session")
def engine():
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine

@pytest.fixture
def db_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()

@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides = {}

@pytest.fixture
def auth_client(client):
    register_response = client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "username": "testuser", "password": "123456"}
    )

    if register_response.status_code == 200:
        token = register_response.cookies.get("access_token")
        user_id = register_response.json()["user"]["id"]
    else:
        login_response = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "password": "123456"}
        )
        token = login_response.cookies.get("access_token")
        user_id = login_response.json()["user"]["id"]

    client.cookies.set("access_token", token)
    client.user_id = user_id
    yield client
    client.cookies.clear()

@pytest.fixture
def second_user_client(client):
    register_response = client.post(
        "/api/auth/register",
        json={"email": "second@example.com", "username": "seconduser", "password": "123456"}
    )

    if register_response.status_code == 200:
        token = register_response.cookies.get("access_token")
        user_id = register_response.json()["user"]["id"]
    else:
        login_response = client.post(
            "/api/auth/login",
            json={"email": "second@example.com", "password": "123456"}
        )
        token = login_response.cookies.get("access_token")
        user_id = login_response.json()["user"]["id"]

    second_client = TestClient(app)
    second_client.cookies.set("access_token", token)
    second_client.user_id = user_id
    yield second_client
    second_client.cookies.clear()