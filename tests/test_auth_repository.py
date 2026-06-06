import pytest
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from types import SimpleNamespace

from app.repositories.auth_repository import AuthRepository
from app.core.database import UserORM

class TestAuthRepository:
    @pytest.fixture
    def mock_db_session(self):
        return Mock(spec=Session)

    @pytest.fixture
    def auth_repo(self, mock_db_session):
        return AuthRepository(mock_db_session)

    @pytest.fixture
    def test_user_orm(self):
        return SimpleNamespace(
            id="123",
            username="testuser",
            email="test@example.com",
            password_hash="hashed_password",
            created_at=1234567890
        )


class TestGetById(TestAuthRepository):
    def test_get_by_id_success(self, auth_repo, mock_db_session, test_user_orm):
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = test_user_orm

        result = auth_repo.get_by_id("123")

        assert result == test_user_orm
        mock_db_session.query.assert_called_once_with(UserORM)
        mock_query.filter.assert_called_once()
        mock_filter.first.assert_called_once()

    def test_get_by_id_not_found(self, auth_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = None

        result = auth_repo.get_by_id("999")

        assert result is None


class TestGetByEmail(TestAuthRepository):
    def test_get_by_email_success(self, auth_repo, mock_db_session, test_user_orm):
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = test_user_orm

        result = auth_repo.get_by_email("test@example.com")

        assert result == test_user_orm
        mock_db_session.query.assert_called_once_with(UserORM)
        mock_query.filter.assert_called_once()
        mock_filter.first.assert_called_once()

    def test_get_by_email_not_found(self, auth_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = None

        result = auth_repo.get_by_email("notfound@example.com")

        assert result is None


class TestGetByUsername(TestAuthRepository):
    def test_get_by_username_success(self, auth_repo, mock_db_session, test_user_orm):
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = test_user_orm

        result = auth_repo.get_by_username("testuser")

        assert result == test_user_orm
        mock_db_session.query.assert_called_once_with(UserORM)
        mock_query.filter.assert_called_once()
        mock_filter.first.assert_called_once()

    def test_get_by_username_not_found(self, auth_repo, mock_db_session):
        mock_query = Mock()
        mock_filter = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.first.return_value = None

        result = auth_repo.get_by_username("notfound")

        assert result is None


class TestGetAllUsers(TestAuthRepository):
    def test_get_all_users_success(self, auth_repo, mock_db_session, test_user_orm):
        mock_query = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.all.return_value = [test_user_orm]

        result = auth_repo.get_all_users()

        assert len(result) == 1
        assert result[0] == test_user_orm
        mock_db_session.query.assert_called_once_with(UserORM)
        mock_query.all.assert_called_once()

    def test_get_all_users_empty(self, auth_repo, mock_db_session):
        mock_query = Mock()
        mock_db_session.query.return_value = mock_query
        mock_query.all.return_value = []

        result = auth_repo.get_all_users()

        assert result == []
        mock_db_session.query.assert_called_once_with(UserORM)
        mock_query.all.assert_called_once()


class TestCreateUser(TestAuthRepository):
    def test_create_user_success(self, auth_repo, mock_db_session):
        with patch('app.repositories.auth_repository.uuid4') as mock_uuid:
            with patch('app.repositories.auth_repository.time') as mock_time:
                mock_uuid.return_value = "new-uuid"
                mock_time.time.return_value = 1234567890

                result = auth_repo.create_user(
                    username="newuser",
                    email="new@example.com",
                    password_hash="hashed_pass"
                )

                assert result.id == "new-uuid"
                assert result.username == "newuser"
                assert result.email == "new@example.com"
                assert result.password_hash == "hashed_pass"
                assert result.created_at == 1234567890
                mock_db_session.add.assert_called_once()
                mock_db_session.commit.assert_called_once()
                mock_db_session.refresh.assert_called_once()

    def test_create_user_handles_db_error(self, auth_repo, mock_db_session):
        with patch('app.repositories.auth_repository.uuid4') as mock_uuid:
            with patch('app.repositories.auth_repository.time') as mock_time:
                mock_uuid.return_value = "new-uuid"
                mock_time.time.return_value = 1234567890
                mock_db_session.commit.side_effect = Exception("DB error")

                with pytest.raises(Exception) as exc:
                    auth_repo.create_user("user", "email@ex.com", "hash")

                assert "DB error" in str(exc.value)
                mock_db_session.rollback.assert_not_called()  # or assert_called if you have rollback


class TestAuthRepositoryIntegration(TestAuthRepository):
    def test_create_and_get_user_flow(self, auth_repo):
        with patch('app.repositories.auth_repository.uuid4') as mock_uuid:
            with patch('app.repositories.auth_repository.time') as mock_time:
                mock_uuid.return_value = "flow-uuid"
                mock_time.time.return_value = 1234567890

                created = auth_repo.create_user("flowuser", "flow@ex.com", "hash123")
                assert created.id == "flow-uuid"

                mock_query = Mock()
                mock_filter = Mock()
                auth_repo.db.query.return_value = mock_query
                mock_query.filter.return_value = mock_filter
                mock_filter.first.return_value = created

                found_by_email = auth_repo.get_by_email("flow@ex.com")
                assert found_by_email is not None
                assert found_by_email.username == "flowuser"

                found_by_username = auth_repo.get_by_username("flowuser")
                assert found_by_username is not None
                assert found_by_username.email == "flow@ex.com"