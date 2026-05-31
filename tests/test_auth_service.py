import pytest
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.services.auth_service import AuthService
from app.models.auth import RegisterRequest, LoginRequest
from app.models.user import UserSchema


class TestAuthService:
    """Тесты для сервиса авторизации"""

    @pytest.fixture
    def mock_db_session(self):
        """Мок сессии БД"""
        return Mock(spec=Session)

    @pytest.fixture
    def mock_repository(self):
        """Мок репозитория"""
        return Mock()

    @pytest.fixture
    def auth_service(self, mock_db_session, mock_repository):
        """Сервис с замоканным репозиторием"""
        service = AuthService(mock_db_session)
        service.repository = mock_repository
        return service

    @pytest.fixture
    def test_user_orm(self):
        """Тестовый пользователь ORM"""
        class MockUser:
            id = "123"
            username = "testuser"
            email = "test@example.com"
            created_at = 1234567890
            password_hash = "hashed_password"
        return MockUser()

    @pytest.fixture
    def test_user_schema(self):
        """Тестовый пользователь Schema"""
        return UserSchema(
            id="123",
            username="testuser",
            email="test@example.com",
            created_at=1234567890
        )


class TestGetAllUsers(TestAuthService):
    """Тесты для метода get_all_users"""

    def test_get_all_users_cache_hit(self, auth_service, mock_repository, test_user_orm):
        """Тест: данные берутся из кэша"""
        with patch('app.services.auth_service.redis_cache') as mock_redis:
            # Кэш содержит данные
            cached_data = [{
                "id": "123",
                "username": "testuser",
                "email": "test@example.com",
                "created_at": 1234567890
            }]
            mock_redis.get.return_value = cached_data

            result = auth_service.get_all_users()

            # Проверяем, что результат из кэша
            assert len(result) == 1
            assert result[0].id == "123"
            assert result[0].username == "testuser"
            # Репозиторий не вызывался
            mock_repository.get_all_users.assert_not_called()

    def test_get_all_users_cache_miss(self, auth_service, mock_repository, test_user_orm, test_user_schema):
        """Тест: данных нет в кэше, берём из БД"""
        with patch('app.services.auth_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            mock_repository.get_all_users.return_value = [test_user_orm]

            result = auth_service.get_all_users()

            # Проверяем результат
            assert len(result) == 1
            assert result[0].username == test_user_orm.username
            # Проверяем, что сохранили в кэш
            mock_redis.set.assert_called_once()

    def test_get_all_users_empty(self, auth_service, mock_repository):
        """Тест: список пользователей пуст"""
        with patch('app.services.auth_service.redis_cache') as mock_redis:
            mock_redis.get.return_value = None
            mock_repository.get_all_users.return_value = []

            result = auth_service.get_all_users()

            assert result == []
            mock_redis.set.assert_called_once_with("all_users", [])


class TestRegister(TestAuthService):
    """Тесты для метода register"""

    def test_register_success(self, auth_service, mock_repository, test_user_orm):
        """Тест: успешная регистрация"""
        with patch('app.services.auth_service.redis_cache') as mock_redis:
            with patch('app.services.auth_service.hash_password') as mock_hash:
                with patch('app.services.auth_service.create_token') as mock_token:
                    # Настройка моков
                    mock_repository.get_by_email.return_value = None
                    mock_repository.get_by_username.return_value = None
                    mock_hash.return_value = "hashed_password"
                    mock_repository.create_user.return_value = test_user_orm
                    mock_token.return_value = "test_token"

                    payload = RegisterRequest(
                        email="test@example.com",
                        username="testuser",
                        password="123456"
                    )

                    result = auth_service.register(payload)

                    # Проверка результата
                    assert result["token"] == "test_token"
                    assert result["user"]["id"] == test_user_orm.id
                    assert result["user"]["username"] == test_user_orm.username
                    # Проверка вызовов
                    mock_repository.get_by_email.assert_called_once_with(payload.email)
                    mock_repository.get_by_username.assert_called_once_with(payload.username)
                    mock_hash.assert_called_once_with(payload.password)
                    mock_repository.create_user.assert_called_once()
                    mock_redis.delete.assert_called_once_with("all_users")

    def test_register_duplicate_email(self, auth_service, mock_repository):
        """Тест: регистрация с существующим email"""
        mock_repository.get_by_email.return_value = Mock()  # email уже существует

        payload = RegisterRequest(
            email="existing@example.com",
            username="newuser",
            password="123456"
        )

        with pytest.raises(HTTPException) as exc:
            auth_service.register(payload)

        assert exc.value.status_code == 400
        assert "Email already registered" in exc.value.detail

    def test_register_duplicate_username(self, auth_service, mock_repository):
        """Тест: регистрация с существующим username"""
        mock_repository.get_by_email.return_value = None
        mock_repository.get_by_username.return_value = Mock()  # username уже существует

        payload = RegisterRequest(
            email="new@example.com",
            username="existinguser",
            password="123456"
        )

        with pytest.raises(HTTPException) as exc:
            auth_service.register(payload)

        assert exc.value.status_code == 400
        assert "Username already taken" in exc.value.detail


class TestLogin(TestAuthService):
    """Тесты для метода login"""

    def test_login_success(self, auth_service, mock_repository, test_user_orm):
        """Тест: успешный вход"""
        with patch('app.services.auth_service.verify_password') as mock_verify:
            with patch('app.services.auth_service.create_token') as mock_token:
                # Настройка моков
                mock_repository.get_by_email.return_value = test_user_orm
                mock_verify.return_value = True
                mock_token.return_value = "test_token"

                payload = LoginRequest(
                    email="test@example.com",
                    password="123456"
                )

                result = auth_service.login(payload)

                assert result["token"] == "test_token"
                assert result["user"]["id"] == test_user_orm.id
                assert result["user"]["username"] == test_user_orm.username
                mock_repository.get_by_email.assert_called_once_with(payload.email)
                mock_verify.assert_called_once_with(payload.password, test_user_orm.password_hash)
                mock_token.assert_called_once_with(test_user_orm.id, test_user_orm.username)

    def test_login_user_not_found(self, auth_service, mock_repository):
        """Тест: пользователь не найден"""
        mock_repository.get_by_email.return_value = None

        payload = LoginRequest(
            email="notfound@example.com",
            password="123456"
        )

        with pytest.raises(HTTPException) as exc:
            auth_service.login(payload)

        assert exc.value.status_code == 401
        assert "Invalid email or password" in exc.value.detail

    def test_login_wrong_password(self, auth_service, mock_repository, test_user_orm):
        """Тест: неверный пароль"""
        with patch('app.services.auth_service.verify_password') as mock_verify:
            mock_repository.get_by_email.return_value = test_user_orm
            mock_verify.return_value = False

            payload = LoginRequest(
                email="test@example.com",
                password="wrongpassword"
            )

            with pytest.raises(HTTPException) as exc:
                auth_service.login(payload)

            assert exc.value.status_code == 401
            assert "Invalid email or password" in exc.value.detail


class TestDeleteUser(TestAuthService):
    """Тесты для метода delete_user"""

    def test_delete_user_success(self, auth_service, mock_repository):
        """Тест: успешное удаление пользователя"""
        with patch('app.services.auth_service.redis_cache') as mock_redis:
            mock_repository.delete_user.return_value = True

            result = auth_service.delete_user("user123")

            assert result is True
            mock_repository.delete_user.assert_called_once_with("user123")
            mock_redis.delete.assert_called_once_with("all_users")

    def test_delete_user_not_found(self, auth_service, mock_repository):
        """Тест: пользователь не найден"""
        with patch('app.services.auth_service.redis_cache') as mock_redis:
            mock_repository.delete_user.return_value = False

            result = auth_service.delete_user("user123")

            assert result is False
            mock_repository.delete_user.assert_called_once_with("user123")
            mock_redis.delete.assert_called_once_with("all_users")