import pytest
import uuid
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from app.repositories.chat_repository import ChatRepository
from types import SimpleNamespace
from app.core.database import ChatORM, ChatParticipantORM

class TestChatRepository:
    @pytest.fixture
    def mock_db_session(self):
        return Mock(spec=Session)

    @pytest.fixture
    def chat_repo(self, mock_db_session):
        return ChatRepository(mock_db_session)

    @pytest.fixture
    def test_chat_orm(self):
        return SimpleNamespace(
            id="chat123",
            name="Test Chat",
            is_group=False,
            created_by="user1",
            created_at=1234567890,
            updated_at=1234567890
        )

    @pytest.fixture
    def test_participant_orm(self):
        return SimpleNamespace(
            id="part123",
            chat_id="chat123",
            user_id="user2",
            joined_at=1234567890
        )


class TestGetExistingPrivateChat(TestChatRepository):
    def test_get_existing_private_chat_found(self, chat_repo, mock_db_session):
        from unittest.mock import MagicMock
        
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_group_by = MagicMock()
        mock_having = MagicMock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.group_by.return_value = mock_group_by
        mock_group_by.having.return_value = mock_having
        
        mock_having.all.return_value = [("chat123",)]
        
        mock_query2 = MagicMock()
        mock_filter2 = MagicMock()
        mock_db_session.query.return_value = mock_query2
        mock_query2.filter.return_value = mock_filter2
        mock_filter2.first.return_value = MagicMock(id="chat123")

        result = chat_repo.get_existing_private_chat("user1", "user2")

        assert result is not None
        assert result.id == "chat123"

    def test_get_existing_private_chat_not_found(self, chat_repo, mock_db_session):
        from unittest.mock import MagicMock
        
        mock_query = MagicMock()
        mock_filter = MagicMock()
        mock_group_by = MagicMock()
        mock_having = MagicMock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.group_by.return_value = mock_group_by
        mock_group_by.having.return_value = mock_having
        mock_having.all.return_value = []

        result = chat_repo.get_existing_private_chat("user1", "user2")

        assert result is None


class TestCreateChat(TestChatRepository):
    def test_create_chat_success(self, chat_repo, mock_db_session):
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "new-chat-uuid"
                mock_time.return_value = 1234567890

                result = chat_repo.create_chat("New Chat", True, "user1")

                assert result.id == "new-chat-uuid"
                assert result.name == "New Chat"
                assert result.is_group is True
                assert result.created_by == "user1"
                mock_db_session.add.assert_called_once()
                mock_db_session.flush.assert_called_once()


class TestDeleteChat:
    def test_delete_chat_success(self, db_session):
        repo = ChatRepository(db_session)
        
        chat = ChatORM(
            id=str(uuid.uuid4()),
            name="Test Chat",
            is_group=False,
            created_by="user123",
            created_at=1234567890,
            updated_at=1234567890
        )
        db_session.add(chat)
        db_session.commit()
        
        chat_id = chat.id
        
        result = repo.delete_chat(chat_id)
        
        assert result is True
        
        db_session.expunge_all()
        deleted = db_session.query(ChatORM).filter(ChatORM.id == chat_id).first()
        assert deleted is None
    
    def test_delete_chat_with_participants(self, db_session):
        repo = ChatRepository(db_session)
        
        chat = ChatORM(
            id=str(uuid.uuid4()),
            name="Test Chat",
            is_group=True,
            created_by="user123",
            created_at=1234567890,
            updated_at=1234567890
        )
        db_session.add(chat)
        db_session.commit()
        
        participant = ChatParticipantORM(
            id=str(uuid.uuid4()),
            chat_id=chat.id,
            user_id="user456",
            joined_at=1234567890
        )
        db_session.add(participant)
        db_session.commit()
        
        chat_id = chat.id
        
        result = repo.delete_chat(chat_id)
        
        assert result is True
        
        db_session.expunge_all()
        deleted = db_session.query(ChatORM).filter(ChatORM.id == chat_id).first()
        assert deleted is None


class TestAddParticipant(TestChatRepository):
    def test_add_participant_success(self, chat_repo, mock_db_session):
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "new-part-uuid"
                mock_time.return_value = 1234567890

                result = chat_repo.add_participant("chat123", "user2")

                assert result.id == "new-part-uuid"
                assert result.chat_id == "chat123"
                assert result.user_id == "user2"
                mock_db_session.add.assert_called_once()
                mock_db_session.flush.assert_called_once()


class TestIsParticipant(TestChatRepository):
    def test_is_participant_true(self, chat_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = SimpleNamespace(id="part123")

        result = chat_repo.is_participant("chat123", "user1")

        assert result is True

    def test_is_participant_false(self, chat_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = None

        result = chat_repo.is_participant("chat123", "user1")

        assert result is False

class TestGetUserChats(TestChatRepository):
    def test_get_user_chats_success(self, chat_repo, mock_db_session, test_chat_orm):
        mock_query = Mock()
        mock_join = Mock()
        mock_filter = Mock()
        mock_all = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.join.return_value = mock_join
        mock_join.filter.return_value = mock_filter
        mock_filter.all.return_value = [test_chat_orm]

        result = chat_repo.get_user_chats("user1")

        assert len(result) == 1
        assert result[0].id == "chat123"

    def test_get_user_chats_empty(self, chat_repo, mock_db_session):
        mock_query = Mock()
        mock_join = Mock()
        mock_filter = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.join.return_value = mock_join
        mock_join.filter.return_value = mock_filter
        mock_filter.all.return_value = []

        result = chat_repo.get_user_chats("user1")

        assert result == []

class TestGetChat(TestChatRepository):
    def test_get_chat_found(self, chat_repo, mock_db_session, test_chat_orm):
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = test_chat_orm

        result = chat_repo.get_chat("chat123")

        assert result is not None
        assert result.id == "chat123"

    def test_get_chat_not_found(self, chat_repo, mock_db_session):
        """Тест: чат не найден"""
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = None

        result = chat_repo.get_chat("chat123")

        assert result is None

class TestChatRepositoryIntegration(TestChatRepository):
    def test_create_and_get_chat_flow(self, chat_repo):
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "flow-chat-uuid"
                mock_time.return_value = 1234567890

                created = chat_repo.create_chat("Flow Chat", False, "user1")
                assert created.id == "flow-chat-uuid"

                mock_query = Mock()
                mock_filter = Mock()
                chat_repo.db.query.return_value = mock_query
                mock_query.filter.return_value = mock_filter
                mock_filter.first.return_value = created

                found = chat_repo.get_chat("flow-chat-uuid")
                assert found is not None
                assert found.name == "Flow Chat"

    def test_add_and_check_participant(self, chat_repo):
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "part-uuid"
                mock_time.return_value = 1234567890

                chat_repo.add_participant("chat123", "user2")

                mock_query = Mock()
                mock_filter = Mock()
                chat_repo.db.query.return_value = mock_query
                mock_query.filter.return_value = mock_filter
                mock_filter.first.return_value = SimpleNamespace(id="part-uuid")

                is_part = chat_repo.is_participant("chat123", "user2")
                assert is_part is True