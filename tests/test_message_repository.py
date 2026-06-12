import pytest
import uuid
import time
import json
from unittest.mock import Mock
from sqlalchemy.orm import Session
from app.repositories.message_repository import MessageRepository
from app.core.database import MessageORM, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Используем реальную тестовую БД
DATABASE_URL = "sqlite:///./test_message.db"

@pytest.fixture(scope="module")
def engine():
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine

@pytest.fixture
def db_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()

@pytest.fixture
def test_chat_id():
    return str(uuid.uuid4())

@pytest.fixture
def test_user_id():
    return str(uuid.uuid4())

@pytest.fixture
def test_other_user_id():
    return str(uuid.uuid4())


class TestMarkAllAsRead:
    """Тесты для метода mark_all_as_read в репозитории"""

    def test_mark_all_as_read_success(self, db_session, test_chat_id, test_user_id, test_other_user_id):
        repo = MessageRepository(db_session)
        
        # Создаём несколько сообщений от другого пользователя
        for i in range(5):
            msg = MessageORM(
                id=str(uuid.uuid4()),
                chat_id=test_chat_id,
                sender_id=test_other_user_id,
                content=f"Message {i}",
                created_at=int(time.time()),
                is_read=False
            )
            db_session.add(msg)
        db_session.commit()
        
        # Отмечаем как прочитанные
        result = repo.mark_all_as_read(test_chat_id, test_user_id)
        
        assert result == 5
        
        # Проверяем что все сообщения отмечены
        unread_count = db_session.query(MessageORM).filter(
            MessageORM.chat_id == test_chat_id,
            MessageORM.sender_id != test_user_id,
            MessageORM.is_read == False
        ).count()
        assert unread_count == 0

    def test_mark_all_as_read_only_others_messages(self, db_session, test_chat_id, test_user_id, test_other_user_id):
        repo = MessageRepository(db_session)
        
        # Создаём свои сообщения
        for i in range(3):
            msg = MessageORM(
                id=str(uuid.uuid4()),
                chat_id=test_chat_id,
                sender_id=test_user_id,
                content=f"Own message {i}",
                created_at=int(time.time()),
                is_read=False
            )
            db_session.add(msg)
        db_session.commit()
        
        # Отмечаем как прочитанные
        result = repo.mark_all_as_read(test_chat_id, test_user_id)
        
        # Свои сообщения не должны быть отмечены
        assert result == 0

    def test_mark_all_as_read_empty_chat(self, db_session, test_chat_id, test_user_id):
        repo = MessageRepository(db_session)
        
        result = repo.mark_all_as_read(test_chat_id, test_user_id)
        assert result == 0

    def test_mark_all_as_read_already_read(self, db_session, test_chat_id, test_user_id, test_other_user_id):
        repo = MessageRepository(db_session)
        
        # Создаём уже прочитанное сообщение
        msg = MessageORM(
            id=str(uuid.uuid4()),
            chat_id=test_chat_id,
            sender_id=test_other_user_id,
            content="Read message",
            created_at=int(time.time()),
            is_read=True
        )
        db_session.add(msg)
        db_session.commit()
        
        result = repo.mark_all_as_read(test_chat_id, test_user_id)
        assert result == 0


class TestGetUnreadCount:
    """Тесты для метода get_unread_count"""

    def test_get_unread_count_success(self, db_session, test_chat_id, test_user_id, test_other_user_id):
        repo = MessageRepository(db_session)
        
        # Создаём несколько непрочитанных сообщений
        for i in range(4):
            msg = MessageORM(
                id=str(uuid.uuid4()),
                chat_id=test_chat_id,
                sender_id=test_other_user_id,
                content=f"Message {i}",
                created_at=int(time.time()),
                is_read=False
            )
            db_session.add(msg)
        db_session.commit()
        
        count = repo.get_unread_count(test_chat_id, test_user_id)
        assert count == 4

    def test_get_unread_count_no_unread(self, db_session, test_chat_id, test_user_id):
        repo = MessageRepository(db_session)
        
        count = repo.get_unread_count(test_chat_id, test_user_id)
        assert count == 0

class TestMessageRepositoryImage:
    def test_create_message_with_is_image_true(self, db_session):
        from app.repositories.message_repository import MessageRepository
        from app.core.database import UserORM, ChatORM
        import uuid
        
        user = UserORM(
            id=str(uuid.uuid4()),
            email="test@example.com",
            username="testuser",
            password_hash="hash",
            created_at=1234567890
        )
        db_session.add(user)
        
        chat = ChatORM(
            id=str(uuid.uuid4()),
            name="Test Chat",
            is_group=False,
            created_by=user.id,
            created_at=1234567890,
            updated_at=1234567890
        )
        db_session.add(chat)
        db_session.commit()
        
        repo = MessageRepository(db_session)
        
        message = repo.create_message(
            chat_id=chat.id,
            sender_id=user.id,
            content="/uploads/images/test.jpg",
            is_image=True
        )
        
        assert message.is_image is True
        assert message.content == "/uploads/images/test.jpg"
        assert message.is_sticker is False
        
        db_session.delete(message)
        db_session.delete(chat)
        db_session.delete(user)
        db_session.commit()
    
    def test_create_message_with_is_image_false_default(self, db_session):
        from app.repositories.message_repository import MessageRepository
        from app.core.database import UserORM, ChatORM
        import uuid
        
        user = UserORM(
            id=str(uuid.uuid4()),
            email="test2@example.com",
            username="testuser2",
            password_hash="hash",
            created_at=1234567890
        )
        db_session.add(user)
        
        chat = ChatORM(
            id=str(uuid.uuid4()),
            name="Test Chat 2",
            is_group=False,
            created_by=user.id,
            created_at=1234567890,
            updated_at=1234567890
        )
        db_session.add(chat)
        db_session.commit()
        
        repo = MessageRepository(db_session)
        
        message = repo.create_message(
            chat_id=chat.id,
            sender_id=user.id,
            content="Hello, world!"
        )
        
        assert message.is_image is False
        assert message.content == "Hello, world!"
        
        db_session.delete(message)
        db_session.delete(chat)
        db_session.delete(user)
        db_session.commit()

class TestMessageRepositoryReply:
    def test_create_message_with_reply_to(self, db_session):
        from app.repositories.message_repository import MessageRepository
        from app.core.database import UserORM, ChatORM
        import uuid
        
        user = UserORM(
            id=str(uuid.uuid4()),
            email="test@example.com",
            username="testuser",
            password_hash="hash",
            created_at=1234567890
        )
        db_session.add(user)
        
        chat = ChatORM(
            id=str(uuid.uuid4()),
            name="Test Chat",
            is_group=False,
            created_by=user.id,
            created_at=1234567890,
            updated_at=1234567890
        )
        db_session.add(chat)
        
        repo = MessageRepository(db_session)
        msg1 = repo.create_message(
            chat_id=chat.id,
            sender_id=user.id,
            content="Original"
        )
        
        msg2 = repo.create_message(
            chat_id=chat.id,
            sender_id=user.id,
            content="Reply",
            reply_to_id=msg1.id
        )
        
        assert msg2.reply_to_id == msg1.id
        assert msg2.content == "Reply"
        
        db_session.rollback()
    
    def test_get_message_with_reply_to_relationship(self, db_session):
        from app.repositories.message_repository import MessageRepository
        from app.core.database import UserORM, ChatORM
        import uuid
        
        user = UserORM(
            id=str(uuid.uuid4()),
            email="test@example.com",
            username="testuser",
            password_hash="hash",
            created_at=1234567890
        )
        db_session.add(user)
        
        chat = ChatORM(
            id=str(uuid.uuid4()),
            name="Test Chat",
            is_group=False,
            created_by=user.id,
            created_at=1234567890,
            updated_at=1234567890
        )
        db_session.add(chat)
        db_session.commit()
        
        repo = MessageRepository(db_session)
        
        msg1 = repo.create_message(
            chat_id=chat.id,
            sender_id=user.id,
            content="Original"
        )
        msg2 = repo.create_message(
            chat_id=chat.id,
            sender_id=user.id,
            content="Reply",
            reply_to_id=msg1.id
        )
        db_session.commit()
        
        assert msg2.reply_to.id == msg1.id
        assert msg2.reply_to.content == "Original"
        
        assert len(msg1.replies) == 1
        assert msg1.replies[0].id == msg2.id
        
        db_session.rollback()

class TestMessageRepositoryImages:
    def test_create_message_with_images(self, db_session):
        from app.repositories.message_repository import MessageRepository
        from app.core.database import UserORM, ChatORM
        import uuid
        import json
        
        user = UserORM(
            id=str(uuid.uuid4()),
            email="test@example.com",
            username="testuser",
            password_hash="hash",
            created_at=1234567890
        )
        db_session.add(user)
        
        chat = ChatORM(
            id=str(uuid.uuid4()),
            name="Test Chat",
            is_group=False,
            created_by=user.id,
            created_at=1234567890,
            updated_at=1234567890
        )
        db_session.add(chat)
        db_session.commit()
        
        repo = MessageRepository(db_session)
        images = ["/uploads/images/1.png", "/uploads/images/2.png"]
        images_json = json.dumps(images)
        
        message = repo.create_message(
            chat_id=chat.id,
            sender_id=user.id,
            content=json.dumps(images),
            is_image=True,
            images=images_json
        )
        
        db_session.commit()
        
        assert message.is_image is True
        assert message.images == images_json
        assert json.loads(message.images) == images
        
        db_session.rollback()
    
    def test_get_message_with_images_returns_parsed(self, db_session):
        from app.repositories.message_repository import MessageRepository
        from app.core.database import UserORM, ChatORM
        import uuid
        import json
        
        user = UserORM(id=str(uuid.uuid4()), email="test@example.com", username="testuser", password_hash="hash", created_at=1234567890)
        db_session.add(user)
        chat = ChatORM(id=str(uuid.uuid4()), name="Test Chat", is_group=False, created_by=user.id, created_at=1234567890, updated_at=1234567890)
        db_session.add(chat)
        db_session.commit()
        
        repo = MessageRepository(db_session)
        images = ["/uploads/images/test.png"]
        message = repo.create_message(
            chat_id=chat.id,
            sender_id=user.id,
            content=json.dumps(images),
            is_image=True,
            images=json.dumps(images)
        )
        db_session.commit()
        
        fetched = db_session.query(MessageORM).filter(MessageORM.id == message.id).first()
        assert fetched.images is not None
        assert json.loads(fetched.images) == images
        
        db_session.rollback()