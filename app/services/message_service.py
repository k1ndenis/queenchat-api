from sqlalchemy.orm import Session
from app.repositories.message_repository import MessageRepository
from app.core.redis import redis_cache
from app.core.database import MessageORM

class MessageService:
    def __init__(self, db: Session):
        self.repo = MessageRepository(db)
    
    def create_message(self, chat_id: str, sender_id: str, content: str = None, sticker_id: str = None):
        is_sticker = sticker_id is not None
        message = self.repo.create_message(chat_id, sender_id, content, sticker_id, is_sticker)
        redis_cache.delete(f"chat_messages:{chat_id}")
        return message
    
    def get_chat_messages(self, chat_id: str, limit: int = None, offset: int = 0) -> list[MessageORM]:
        return self.repo.get_chat_messages(chat_id, limit, offset)

    def mark_as_read(self, message_id: str, user_id: str) -> MessageORM | None:
        return self.repo.mark_as_read(message_id, user_id)

    def get_last_message(self, chat_id: str) -> MessageORM | None:
        return self.repo.get_last_message(chat_id)
    
    def get_unread_count(self, chat_id: str, user_id: str) -> int:
        return self.repo.get_unread_count(chat_id, user_id)

    def mark_all_as_read(self, chat_id: str, user_id: str) -> int:
        return self.repo.mark_all_as_read(chat_id, user_id)