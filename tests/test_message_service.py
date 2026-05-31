import pytest
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from app.services.message_service import MessageService


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
        class MockMessage:
            id = "msg123"
            chat_id = "chat123"
            sender_id = "user1"
            content = "Hello, world!"
            created_at = 1234567890
            is_read = False
        return MockMessage()

    @pytest.fixture
    def test_message_dict(self):
        return {
            "id": "msg123",
            "chat_id": "chat123",
            "sender_id": "user1",
            "content": "Hello, world!",
            "created_at": 1234567890,
            "is_read": False
        }


class TestCreateMessage(TestMessageService):
    def test_create_message_success(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            message_service.repo.create_message.return_value = test_message_orm

            result = message_service.create_message("chat123", "user1", "Hello, world!")

            assert result.id == test_message_orm.id
            assert result.content == test_message_orm.content
            mock_redis.delete.assert_called_once_with("chat_messages:chat123")

    def test_create_message_clears_cache(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            message_service.repo.create_message.return_value = test_message_orm

            message_service.create_message("chat123", "user1", "Test")

            mock_redis.delete.assert_called_once_with("chat_messages:chat123")

    def test_create_message_empty_content(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache'):
            message_service.repo.create_message.return_value = test_message_orm

            result = message_service.create_message("chat123", "user1", "")

            assert result is not None


class TestGetChatMessages(TestMessageService):
    def test_get_chat_messages_cache_hit(self, message_service, test_message_dict):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = [test_message_dict]

            result = message_service.get_chat_messages("chat123")

            assert len(result) == 1
            assert result[0]["id"] == "msg123"  # ← словарь, используем []
            message_service.repo.get_chat_messages.assert_not_called()

    def test_get_chat_messages_cache_miss(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            message_service.repo.get_chat_messages.return_value = [test_message_orm]

            result = message_service.get_chat_messages("chat123")

            assert len(result) == 1
            assert isinstance(result[0], dict)
            assert result[0]["id"] == test_message_orm.id  # ← доступ через []
            mock_redis.set.assert_called_once()

    def test_get_chat_messages_empty(self, message_service):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            message_service.repo.get_chat_messages.return_value = []

            result = message_service.get_chat_messages("chat123")

            assert result == []
            mock_redis.set.assert_called_once()

    def test_get_chat_messages_with_limit_and_offset(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            message_service.repo.get_chat_messages.return_value = [test_message_orm]

            result = message_service.get_chat_messages("chat123", limit=20, offset=10)

            assert len(result) == 1
            message_service.repo.get_chat_messages.assert_called_once_with("chat123", 20, 10)

    def test_get_chat_messages_cache_key_includes_params(self, message_service):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            message_service.repo.get_chat_messages.return_value = []

            message_service.get_chat_messages("chat123", limit=30, offset=5)

            call_args = mock_redis.set.call_args[0]
            assert call_args[0] == "chat_messages:chat123:30:5"

    def test_get_chat_messages_different_params_different_cache(self, message_service):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            message_service.repo.get_chat_messages.return_value = []

            message_service.get_chat_messages("chat123", limit=10, offset=0)
            message_service.get_chat_messages("chat123", limit=20, offset=0)

            assert mock_redis.set.call_count == 2
            first_key = mock_redis.set.call_args_list[0][0][0]
            second_key = mock_redis.set.call_args_list[1][0][0]
            assert first_key != second_key

    def test_get_chat_messages_returns_dict_structure(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            message_service.repo.get_chat_messages.return_value = [test_message_orm]

            result = message_service.get_chat_messages("chat123")

            assert isinstance(result, list)
            assert isinstance(result[0], dict)
            assert "id" in result[0]
            assert "chat_id" in result[0]
            assert "sender_id" in result[0]
            assert "content" in result[0]
            assert "created_at" in result[0]
            assert "is_read" in result[0]


class TestMessageServiceIntegration(TestMessageService):
    def test_create_message_invalidates_cache(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            message_service.repo.get_chat_messages.return_value = []
            message_service.get_chat_messages("chat123")

            mock_redis.reset_mock()
            message_service.repo.create_message.return_value = test_message_orm
            message_service.create_message("chat123", "user1", "New message")

            mock_redis.delete.assert_called_once_with("chat_messages:chat123")

    def test_get_messages_after_create_returns_new_data(self, message_service, test_message_orm):
        with patch('app.services.message_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            message_service.repo.get_chat_messages.return_value = []
            result1 = message_service.get_chat_messages("chat123")
            assert result1 == []

            mock_redis.delete.reset_mock()
            message_service.repo.create_message.return_value = test_message_orm
            message_service.create_message("chat123", "user1", "Hello")

            mock_redis.get.return_value = None
            message_service.repo.get_chat_messages.return_value = [test_message_orm]
            result2 = message_service.get_chat_messages("chat123")
            assert len(result2) == 1
            assert isinstance(result2[0], dict)
            assert result2[0]["id"] == test_message_orm.id
            assert result2[0]["content"] == "Hello, world!"