import pytest
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from app.services.message_service import MessageService
from types import SimpleNamespace


class TestMessageService:
    @pytest.fixture
    def mock_db_session(self):
        return Mock(spec=Session)

    @pytest.fixture
    def mock_repo(self):
        return Mock()

    @pytest.fixture
    def message_service(self, mock_db_session, mock_repo):
        service = MessageService(mock_db_session)
        service.repo = mock_repo
        return service

    @pytest.fixture
    def test_message_orm(self):
        return SimpleNamespace(
            id="msg123",
            chat_id="chat123",
            sender_id="user1",
            content="Hello, world!",
            created_at=1234567890,
            is_read=False
        )


class TestCreateMessage(TestMessageService):
    def test_create_message_success(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            message_service.repo.create_message.return_value = test_message_orm

            result = message_service.create_message("chat123", "user1", "Hello, world!")

            assert result.id == test_message_orm.id
            assert result.content == test_message_orm.content
            mock_redis.delete.assert_called_once_with("chat_messages:chat123")


class TestGetChatMessages(TestMessageService):
    def test_get_chat_messages_direct(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache.get') as mock_get:
            mock_get.return_value = None
            message_service.repo.get_chat_messages.return_value = [test_message_orm]

            result = message_service.get_chat_messages("chat123")

            assert len(result) == 1
            assert result[0].id == "msg123"

    def test_get_chat_messages_with_limit(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache'):
            message_service.repo.get_chat_messages.return_value = [test_message_orm]

            result = message_service.get_chat_messages("chat123", limit=20, offset=10)

            assert len(result) == 1
            message_service.repo.get_chat_messages.assert_called_once_with("chat123", 20, 10)

    def test_get_chat_messages_empty(self, message_service):
        with patch('app.services.message_service.redis_cache'):
            message_service.repo.get_chat_messages.return_value = []

            result = message_service.get_chat_messages("chat123")

            assert result == []

    def test_get_chat_messages_returns_orm_structure(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache'):
            message_service.repo.get_chat_messages.return_value = [test_message_orm]

            result = message_service.get_chat_messages("chat123")

            assert isinstance(result, list)
            assert hasattr(result[0], 'id')
            assert hasattr(result[0], 'chat_id')
            assert hasattr(result[0], 'sender_id')
            assert hasattr(result[0], 'content')
            assert hasattr(result[0], 'created_at')
            assert hasattr(result[0], 'is_read')


class TestMarkAsRead(TestMessageService):
    def test_mark_as_read_success(self, message_service, test_message_orm):
        message_service.repo.mark_as_read.return_value = test_message_orm

        result = message_service.mark_as_read("msg123", "user2")

        assert result is not None
        message_service.repo.mark_as_read.assert_called_once_with("msg123", "user2")

    def test_mark_as_read_not_found(self, message_service):
        message_service.repo.mark_as_read.return_value = None

        result = message_service.mark_as_read("msg123", "user2")

        assert result is None


class TestMessageServiceIntegration(TestMessageService):
    def test_create_message_invalidates_cache(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            message_service.repo.create_message.return_value = test_message_orm
            message_service.create_message("chat123", "user1", "New message")

            mock_redis.delete.assert_called_once_with("chat_messages:chat123")

    def test_get_messages_after_create_returns_new_data(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache.get') as mock_get:
            mock_get.return_value = None
            
            message_service.repo.get_chat_messages.return_value = []
            result1 = message_service.get_chat_messages("chat123")
            assert result1 == []

            message_service.repo.create_message.return_value = test_message_orm
            message_service.create_message("chat123", "user1", "Hello")

            message_service.repo.get_chat_messages.return_value = [test_message_orm]
            result2 = message_service.get_chat_messages("chat123")
            assert len(result2) == 1
            assert hasattr(result2[0], 'id')