import pytest
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from app.repositories.message_repository import MessageRepository
from app.core.database import MessageORM
from types import SimpleNamespace


class TestMessageRepository:
    @pytest.fixture
    def mock_db_session(self):
        return Mock(spec=Session)

    @pytest.fixture
    def message_repo(self, mock_db_session):
        return MessageRepository(mock_db_session)

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


class TestCreateMessage(TestMessageRepository):
    def test_create_message_success(self, message_repo, mock_db_session):
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
                mock_db_session.flush.assert_called_once()

    def test_create_message_handles_db_error(self, message_repo, mock_db_session):
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "new-msg-uuid"
                mock_time.return_value = 1234567890
                mock_db_session.flush.side_effect = Exception("DB error")

                with pytest.raises(Exception) as exc:
                    message_repo.create_message("chat123", "user1", "Test")

                assert "DB error" in str(exc.value)


class TestGetChatMessages(TestMessageRepository):
    def test_get_chat_messages_success(self, message_repo, mock_db_session, test_message_orm):
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.all.return_value = [test_message_orm]

        result = message_repo.get_chat_messages("chat123")

        assert len(result) == 1
        assert result[0].id == "msg123"
        mock_db_session.query.assert_called_once_with(MessageORM)

    def test_get_chat_messages_default_params(self, message_repo, mock_db_session, test_message_orm):
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.all.return_value = [test_message_orm]

        result = message_repo.get_chat_messages("chat123")

        assert len(result) == 1

    def test_get_chat_messages_empty(self, message_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.all.return_value = []

        result = message_repo.get_chat_messages("chat123")

        assert result == []

    def test_get_chat_messages_order_by_asc(self, message_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.all.return_value = []

        message_repo.get_chat_messages("chat123")

        mock_filter.order_by.assert_called_once()

    def test_get_chat_messages_different_chats(self, message_repo, mock_db_session, test_message_orm):
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.all.return_value = [test_message_orm]

        result1 = message_repo.get_chat_messages("chat123")
        result2 = message_repo.get_chat_messages("chat456")

        assert len(result1) == 1
        assert len(result2) == 1
        assert mock_query.filter.call_count >= 2


class TestMessageRepositoryIntegration(TestMessageRepository):
    def test_create_and_get_message_flow(self, message_repo):
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "flow-msg-uuid"
                mock_time.return_value = 1234567890

                created = message_repo.create_message("chat123", "user1", "Integration test")
                assert created.id == "flow-msg-uuid"

                mock_query = Mock()
                mock_filter = Mock()
                mock_order_by = Mock()
                
                message_repo.db.query.return_value = mock_query
                mock_query.filter.return_value = mock_filter
                mock_filter.order_by.return_value = mock_order_by
                mock_order_by.all.return_value = [created]

                messages = message_repo.get_chat_messages("chat123")
                assert len(messages) == 1
                assert messages[0].content == "Integration test"