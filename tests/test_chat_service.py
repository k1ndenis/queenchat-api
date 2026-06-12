import pytest
from unittest.mock import MagicMock, Mock, patch
from sqlalchemy.orm import Session
from app.services.chat_service import ChatService
from types import SimpleNamespace

class TestChatService:
    @pytest.fixture
    def mock_db_session(self):
        return Mock(spec=Session)

    @pytest.fixture
    def mock_repo(self):
        return Mock()

    @pytest.fixture
    def mock_auth_repo(self):
        return Mock()

    @pytest.fixture
    def chat_service(self, mock_db_session, mock_repo, mock_auth_repo):
        service = ChatService(mock_db_session)
        service.repo = mock_repo
        service.auth_repo = mock_auth_repo
        return service

    @pytest.fixture
    def test_chat_orm(self):
        user1 = SimpleNamespace(id="user1", username="user1")
        user2 = SimpleNamespace(id="user2", username="user2")
        
        class MockChat:
            id = "chat123"
            name = "Test Chat"
            is_group = True
            created_by = "user1"
            created_at = 1234567890
            updated_at = 1234567890
            participants = [user1, user2]
        return MockChat()

    @pytest.fixture
    def test_user(self):
        return SimpleNamespace(
            id="user1",
            username="user1",
            email="user1@example.com"
        )


class TestGetUserChats(TestChatService):
    def test_get_user_chats_cache_hit(self, chat_service):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            cached_data = [{
                "id": "chat123",
                "name": "Test Chat",
                "is_group": True,
                "created_by": "user1",
                "created_at": 1234567890,
                "updated_at": 1234567890,
                "participants": []
            }]
            mock_redis.get.return_value = cached_data

            result = chat_service.get_user_chats("user1")

            assert len(result) == 1
            assert result[0].id == "chat123"
            chat_service.repo.get_user_chats.assert_not_called()

    def test_get_user_chats_cache_miss(self, chat_service, test_chat_orm):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            chat_service.repo.get_user_chats.return_value = [test_chat_orm]

            result = chat_service.get_user_chats("user1")

            assert len(result) == 1
            assert result[0].id == test_chat_orm.id
            mock_redis.set.assert_called_once()

    def test_get_user_chats_empty(self, chat_service):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            chat_service.repo.get_user_chats.return_value = []

            result = chat_service.get_user_chats("user1")

            assert result == []
            mock_redis.set.assert_called_once()

class TestGetChat(TestChatService):
    def test_get_chat_cache_hit(self, chat_service):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            cached_data = {
                "id": "chat123",
                "name": "Test Chat",
                "is_group": True,
                "created_by": "user1",
                "created_at": 1234567890,
                "updated_at": 1234567890,
                "participants": []
            }
            mock_redis.get.return_value = cached_data

            result = chat_service.get_chat("chat123")

            assert result.id == "chat123"
            chat_service.repo.get_chat.assert_not_called()

    def test_get_chat_cache_miss(self, mock_redis_cache, mock_auth_repo, mock_chat_repo, db_session):
        mock_redis_cache.get.return_value = None
        
        mock_user = SimpleNamespace(
            id="user1",
            username="testuser",
            email="test@test.com",
            avatar=None
        )
        
        test_chat_orm = SimpleNamespace(
            id="chat1",
            name="Test Chat",
            is_group=False,
            created_by="user1",
            created_at=1234567890,
            updated_at=1234567890,
            participants=[mock_user]
        )
        
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            chat_service = ChatService(db_session)
            chat_service.repo = mock_chat_repo
            chat_service.repo.get_chat.return_value = test_chat_orm
            chat_service.auth_repo = mock_auth_repo
            
            result = chat_service.get_chat("chat1")
            
            assert result is not None
            assert result.id == "chat1"
            mock_redis.get.assert_called_once_with("chat:chat1")

    def test_get_chat_not_found(self, chat_service):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            chat_service.repo.get_chat.return_value = None

            result = chat_service.get_chat("chat123")

            assert result is None
            mock_redis.set.assert_not_called()


class TestCreateChat(TestChatService):
    def test_create_chat_success(self, chat_service, test_chat_orm, test_user):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            with patch('app.services.chat_service.time') as mock_time:
                mock_time.time.return_value = 1234567890
                chat_service.repo.create_chat.return_value = test_chat_orm
                chat_service.auth_repo.get_by_id.return_value = test_user

                result = chat_service.create_chat(
                    name="Test Chat",
                    is_group=True,
                    created_by="user1",
                    participant_ids=["user2"]
                )

                assert result.id == test_chat_orm.id
                assert result.name == test_chat_orm.name
                assert result.is_group is True
                assert mock_redis.delete.call_count >= 2

    def test_create_chat_clears_cache_for_all_participants(self, chat_service, test_chat_orm, test_user):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            with patch('app.services.chat_service.time') as mock_time:
                mock_time.time.return_value = 1234567890
                chat_service.repo.create_chat.return_value = test_chat_orm
                chat_service.auth_repo.get_by_id.return_value = test_user

                chat_service.create_chat(
                    name="Test Chat",
                    is_group=False,
                    created_by="user1",
                    participant_ids=["user2", "user3"]
                )

                expected_calls = [
                    ("user_chats:user1",),
                    ("user_chats:user2",),
                    ("user_chats:user3",)
                ]
                actual_calls = [call[0] for call in mock_redis.delete.call_args_list]
                assert ("user_chats:user1",) in actual_calls
                assert ("user_chats:user2",) in actual_calls
                assert ("user_chats:user3",) in actual_calls


class TestDeleteChat(TestChatService):
    def test_delete_chat_success(self, chat_service, test_chat_orm):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            chat_service.repo.get_chat.return_value = test_chat_orm
            chat_service.repo.delete_chat.return_value = True

            result = chat_service.delete_chat("chat123")

            assert result is True
            mock_redis.delete.assert_any_call("chat:chat123")
            mock_redis.delete.assert_any_call("user_chats:user1")
            mock_redis.delete.assert_any_call("user_chats:user2")

    def test_delete_chat_not_found(self, chat_service):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            chat_service.repo.get_chat.return_value = None
            chat_service.repo.delete_chat.return_value = False

            result = chat_service.delete_chat("chat123")

            assert result is False
            mock_redis.delete.assert_called_once_with("chat:chat123")


class TestIsParticipant(TestChatService):
    def test_is_participant_true(self, chat_service):
        chat_service.repo.is_participant.return_value = True

        result = chat_service.is_participant("chat123", "user1")

        assert result is True

    def test_is_participant_false(self, chat_service):
        chat_service.repo.is_participant.return_value = False

        result = chat_service.is_participant("chat123", "user1")

        assert result is False


class TestAddParticipant(TestChatService):
    def test_add_participant_success(self, chat_service):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            chat_service.repo.add_participant.return_value = {"success": True}

            result = chat_service.add_participant("chat123", "user2")

            assert result == {"success": True}
            mock_redis.delete.assert_any_call("user_chats:user2")
            mock_redis.delete.assert_any_call("chat:chat123")


class TestRemoveParticipant(TestChatService):
    def test_remove_participant_success(self, chat_service):
        with patch('app.services.chat_service.redis_cache') as mock_redis:
            chat_service.repo.remove_participant.return_value = {"success": True}

            result = chat_service.remove_participant("chat123", "user2")

            assert result == {"success": True}
            mock_redis.delete.assert_any_call("user_chats:user2")
            mock_redis.delete.assert_any_call("chat:chat123")