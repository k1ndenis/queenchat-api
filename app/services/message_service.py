from sqlalchemy.orm import Session
from app.repositories.message_repository import MessageRepository

class MessageService:
    def __init__(self, db: Session):
        self.repo = MessageRepository(db)
    
    def create_message(self, chat_id: str, sender_id: str, content: str):
        return self.repo.create_message(chat_id, sender_id, content)
    
    def get_chat_messages(self, chat_id: str, limit: int = 50, offset: int = 0):
        return self.repo.get_chat_messages(chat_id, limit, offset)