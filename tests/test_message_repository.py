import pytest
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from app.repositories.message_repository import MessageRepository
from app.core.database import MessageORM
from types import SimpleNamespace


class TestMessageRepository:
    """Тесты для репозитория сообщений"""

    @pytest.fixture
    def mock_db_session(self):
        """Мок сессии БД"""
        return Mock(spec=Session)

    @pytest.fixture
    def message_repo(self, mock_db_session):
        """Репозиторий с замоканной сессией"""
        return MessageRepository(mock_db_session)

    @pytest.fixture
    def test_message_orm(self):
        """Тестовое сообщение ORM"""
        return SimpleNamespace(
            id="msg123",
            chat_id="chat123",
            sender_id="user1",
            content="Hello, world!",
            created_at=1234567890,
            is_read=False
        )


class TestCreateMessage(TestMessageRepository):
    """Тесты для метода create_message"""

    def test_create_message_success(self, message_repo, mock_db_session):
        """Тест: успешное создание сообщения"""
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "new-msg-uuid"
                mock_time.return_value = 1234567890

                result = message_repo.create_message("chat123", "user1", "Test message")

                assert result.id == "new-msg-uuid"
                assert result.chat_id == "chat123"
                assert result.sender_id == "user1"
                assert result.content == "Test message"
                assert result.created_at == 1234567890
                assert result.is_read is False
                mock_db_session.add.assert_called_once()
                mock_db_session.commit.assert_called_once()
                mock_db_session.refresh.assert_called_once()

    def test_create_message_empty_content(self, message_repo, mock_db_session):
        """Тест: создание сообщения с пустым содержимым"""
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "new-msg-uuid"
                mock_time.return_value = 1234567890

                result = message_repo.create_message("chat123", "user1", "")

                assert result.content == ""
                mock_db_session.add.assert_called_once()

    def test_create_message_handles_db_error(self, message_repo, mock_db_session):
        """Тест: ошибка при создании сообщения"""
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "new-msg-uuid"
                mock_time.return_value = 1234567890
                mock_db_session.commit.side_effect = Exception("DB error")

                with pytest.raises(Exception) as exc:
                    message_repo.create_message("chat123", "user1", "Test")

                assert "DB error" in str(exc.value)


class TestGetChatMessages(TestMessageRepository):
    """Тесты для метода get_chat_messages"""

    def test_get_chat_messages_success(self, message_repo, mock_db_session, test_message_orm):
        """Тест: успешное получение сообщений"""
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        mock_offset = Mock()
        mock_limit = Mock()
        mock_all = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.offset.return_value = mock_offset
        mock_offset.limit.return_value = mock_limit
        mock_limit.all.return_value = [test_message_orm]

        result = message_repo.get_chat_messages("chat123", limit=20, offset=10)

        assert len(result) == 1
        assert result[0].id == "msg123"
        mock_db_session.query.assert_called_once_with(MessageORM)
        mock_query.filter.assert_called_once()
        mock_filter.order_by.assert_called_once()
        mock_order_by.offset.assert_called_once_with(10)
        mock_offset.limit.assert_called_once_with(20)

    def test_get_chat_messages_default_params(self, message_repo, mock_db_session, test_message_orm):
        """Тест: получение сообщений с параметрами по умолчанию"""
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        mock_offset = Mock()
        mock_limit = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.offset.return_value = mock_offset
        mock_offset.limit.return_value = mock_limit
        mock_limit.all.return_value = [test_message_orm]

        result = message_repo.get_chat_messages("chat123")

        assert len(result) == 1
        mock_order_by.offset.assert_called_once_with(0)
        mock_offset.limit.assert_called_once_with(50)

    def test_get_chat_messages_empty(self, message_repo, mock_db_session):
        """Тест: сообщений нет"""
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        mock_offset = Mock()
        mock_limit = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.offset.return_value = mock_offset
        mock_offset.limit.return_value = mock_limit
        mock_limit.all.return_value = []

        result = message_repo.get_chat_messages("chat123")

        assert result == []

    def test_get_chat_messages_order_by_desc(self, message_repo, mock_db_session):
        """Тест: сообщения сортируются по убыванию created_at"""
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.offset.return_value = Mock()
        mock_order_by.offset.return_value.limit.return_value.all.return_value = []

        message_repo.get_chat_messages("chat123")

        # Проверяем, что order_by вызван с desc
        mock_filter.order_by.assert_called_once()
        # Проверяем, что передан правильный атрибут
        call_args = mock_filter.order_by.call_args[0][0]
        assert str(call_args) == str(MessageORM.created_at.desc())

    def test_get_chat_messages_different_chats(self, message_repo, mock_db_session, test_message_orm):
        """Тест: сообщения для разных чатов не смешиваются"""
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        mock_offset = Mock()
        mock_limit = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.offset.return_value = mock_offset
        mock_offset.limit.return_value = mock_limit
        mock_limit.all.return_value = [test_message_orm]

        result1 = message_repo.get_chat_messages("chat123")
        result2 = message_repo.get_chat_messages("chat456")

        assert len(result1) == 1
        assert len(result2) == 1
        # Проверяем, что filter вызывался с разными chat_id
        assert mock_query.filter.call_count == 2


class TestMessageRepositoryIntegration(TestMessageRepository):
    """Интеграционные тесты"""

    def test_create_and_get_message_flow(self, message_repo):
        """Тест: создание и получение сообщения"""
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "flow-msg-uuid"
                mock_time.return_value = 1234567890

                created = message_repo.create_message("chat123", "user1", "Integration test")
                assert created.id == "flow-msg-uuid"
                assert created.content == "Integration test"

                # Мокаем получение
                mock_query = Mock()
                mock_filter = Mock()
                mock_order_by = Mock()
                mock_offset = Mock()
                mock_limit = Mock()
                
                message_repo.db.query.return_value = mock_query
                mock_query.filter.return_value = mock_filter
                mock_filter.order_by.return_value = mock_order_by
                mock_order_by.offset.return_value = mock_offset
                mock_offset.limit.return_value = mock_limit
                mock_limit.all.return_value = [created]

                messages = message_repo.get_chat_messages("chat123")
                assert len(messages) == 1
                assert messages[0].content == "Integration test"