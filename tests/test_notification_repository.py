import pytest
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from app.repositories.notification_repository import NotificationRepository

class TestNotificationRepository:
    @pytest.fixture
    def mock_db_session(self):
        return Mock(spec=Session)

    @pytest.fixture
    def notification_repo(self, mock_db_session):
        return NotificationRepository(mock_db_session)

    def test_create_notification(self, notification_repo, mock_db_session):
        with patch('uuid.uuid4') as mock_uuid:
            with patch('time.time') as mock_time:
                mock_uuid.return_value = "test-uuid"
                mock_time.return_value = 1234567890
                
                result = notification_repo.create(
                    user_id="user123",
                    chat_id="chat123",
                    title="Test",
                    message="Test message",
                    type="info"
                )
                
                assert result is not None
                mock_db_session.add.assert_called_once()
                mock_db_session.commit.assert_called_once()
                mock_db_session.refresh.assert_called_once()

    def test_get_by_user(self, notification_repo, mock_db_session):
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
        
        result = notification_repo.get_by_user("user123", limit=50, offset=0)
        
        assert result == []
        mock_query.filter.assert_called_once()

    def test_get_unread_count(self, notification_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        mock_count = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.count.return_value = 5
        
        result = notification_repo.get_unread_count("user123")
        
        assert result == 5

    def test_mark_as_read(self, notification_repo, mock_db_session):
        mock_notification = Mock()
        mock_notification.is_read = False
        
        mock_query = Mock()
        mock_filter = Mock()
        mock_first = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = mock_notification
        
        result = notification_repo.mark_as_read("notif123", "user123")
        
        assert result is True
        assert mock_notification.is_read is True
        mock_db_session.commit.assert_called_once()

    def test_mark_all_as_read(self, notification_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        
        notification_repo.mark_all_as_read("user123")
        
        mock_filter.update.assert_called_once_with({"is_read": True})
        mock_db_session.commit.assert_called_once()

    def test_delete_old_notifications(self, notification_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.delete.return_value = 10
        
        result = notification_repo.delete_old_notifications("user123", days=30)
        
        assert result == 10
        mock_db_session.commit.assert_called_once()

    def test_delete_all_read(self, notification_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.delete.return_value = 5
        
        result = notification_repo.delete_all_read("user123")
        
        assert result == 5
        mock_db_session.commit.assert_called_once()

    def test_limit_notifications(self, notification_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        mock_offset = Mock()
        
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.offset.return_value = mock_offset
        mock_offset.all.return_value = [("id1",), ("id2",)]
        
        notification_repo.limit_notifications("user123", max_count=50)
        
        mock_db_session.commit.assert_called_once()