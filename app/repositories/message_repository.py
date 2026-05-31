from sqlalchemy.orm import Session
import uuid
import time
from app.core.database import MessageORM

class MessageRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_message(self, chat_id: str, sender_id: str, content: str) -> MessageORM:
        message = MessageORM(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            sender_id=sender_id,
            content=content,
            created_at=int(time.time()),
            is_read=False
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def get_chat_messages(self, chat_id: str, limit: int = 50, offset: int = 0) -> list[MessageORM]:
        return self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id
        ).order_by(
            MessageORM.created_at.desc()
        ).offset(offset).limit(limit).all()