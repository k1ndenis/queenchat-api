from sqlalchemy.orm import Session
from app.repositories.message_repository import MessageRepository
from app.core.redis import redis_cache

class MessageService:
    def __init__(self, db: Session):
        self.repo = MessageRepository(db)
    
    def create_message(self, chat_id: str, sender_id: str, content: str):
        message = self.repo.create_message(chat_id, sender_id, content)
        redis_cache.delete(f"chat_messages:{chat_id}")
        return message
    
    def get_chat_messages(self, chat_id: str, limit: int = 50, offset: int = 0):
        cache_key = f"chat_messages:{chat_id}:{limit}:{offset}"
        cached = redis_cache.get(cache_key)
        if cached:
            return cached
        
        messages = self.repo.get_chat_messages(chat_id, limit, offset)
        
        messages_dict = [
            {
                "id": msg.id,
                "chat_id": msg.chat_id,
                "sender_id": msg.sender_id,
                "content": msg.content,
                "created_at": msg.created_at,
                "is_read": msg.is_read
            }
            for msg in messages
        ]
        
        redis_cache.set(cache_key, messages_dict)
        return messages