from sqlalchemy.orm import Session
from app.repositories.message_repository import MessageRepository
from app.core.redis import redis_cache


class MessageService:
    def __init__(self, db: Session):
        self.repo = MessageRepository(db)
    
    def create_message(self, chat_id: str, sender_id: str, content: str = None, sticker_id: str = None):
        is_sticker = sticker_id is not None
        message = self.repo.create_message(chat_id, sender_id, content, sticker_id, is_sticker)
        redis_cache.delete(f"chat_messages:{chat_id}")
        return message
    
    def get_chat_messages(self, chat_id: str, limit: int = None, offset: int = 0) -> list[MessageORM]:
        """Получить сообщения чата. Если limit=None - все сообщения"""
        return self.repo.get_chat_messages(chat_id, limit, offset)

    def mark_as_read(self, message_id: str, user_id: str) -> bool:
        return self.repo.mark_as_read(message_id, user_id)