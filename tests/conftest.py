import sys
import os
from unittest.mock import Mock

os.environ["TESTING"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["DB_HOST"] = "localhost"
os.environ["DB_PORT"] = "5432"
os.environ["DB_NAME"] = "test"
os.environ["DB_USER"] = "test"
os.environ["DB_PASSWORD"] = "test"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from main import app
from app.core.database import Base
from app.core.dependency import get_db
from app.core.websocket import manager
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.message_service import MessageService
from app.services.notification_service import NotificationService
from app.repositories.auth_repository import AuthRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.notification_repository import NotificationRepository

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
    response = client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "username": "testuser", "password": "123456"}
    )
    if response.status_code != 200:
        response = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "password": "123456"}
        )
    token = response.cookies.get("access_token")
    client.cookies.set("access_token", token)
    client.user_id = response.json().get("user", {}).get("id", "test_user_id")
    yield client

@pytest.fixture
def second_user_client():
    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"email": "second@example.com", "username": "seconduser", "password": "123456"}
    )
    if response.status_code != 200:
        response = client.post(
            "/api/auth/login",
            json={"email": "second@example.com", "password": "123456"}
        )
    token = response.cookies.get("access_token")
    client.cookies.set("access_token", token)
    client.user_id = response.json().get("user", {}).get("id")
    yield client

@pytest.fixture
def auth_repo(db_session):
    return AuthRepository(db_session)

@pytest.fixture
def chat_repo(db_session):
    return ChatRepository(db_session)

@pytest.fixture
def message_repo(db_session):
    return MessageRepository(db_session)

@pytest.fixture
def notification_repo(db_session):
    return NotificationRepository(db_session)

@pytest.fixture
def auth_service(db_session):
    return AuthService(db_session)

@pytest.fixture
def chat_service(db_session):
    return ChatService(db_session)

@pytest.fixture
def message_service(db_session):
    return MessageService(db_session)

@pytest.fixture
def notification_service(db_session):
    return NotificationService(db_session)

@pytest.fixture
def mock_db_session():
    return Mock()

@pytest.fixture
def mock_redis():
    from unittest.mock import Mock
    return Mock()