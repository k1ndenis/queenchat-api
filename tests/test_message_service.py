import pytest
from unittest.mock import Mock
from sqlalchemy.orm import Session
from app.services.message_service import MessageService
from app.repositories.message_repository import MessageRepository


class TestMarkAllAsRead:
    """Тесты для метода mark_all_as_read в сервисе"""

    def test_mark_all_as_read_calls_repository(self):
        # Создаём мок репозитория
        mock_repo = Mock(spec=MessageRepository)
        mock_repo.mark_all_as_read.return_value = 5
        
        # Создаём сервис с моком
        service = MessageService(Mock(spec=Session))
        service.repo = mock_repo
        
        result = service.mark_all_as_read("chat123", "user123")
        
        assert result == 5
        mock_repo.mark_all_as_read.assert_called_once_with("chat123", "user123")

    def test_mark_all_as_read_returns_count(self):
        mock_repo = Mock(spec=MessageRepository)
        mock_repo.mark_all_as_read.return_value = 3
        
        service = MessageService(Mock(spec=Session))
        service.repo = mock_repo
        
        result = service.mark_all_as_read("chat123", "user123")
        
        assert result == 3

    def test_mark_all_as_read_empty_result(self):
        mock_repo = Mock(spec=MessageRepository)
        mock_repo.mark_all_as_read.return_value = 0
        
        service = MessageService(Mock(spec=Session))
        service.repo = mock_repo
        
        result = service.mark_all_as_read("chat123", "user123")
        
        assert result == 0