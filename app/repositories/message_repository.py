# app/repositories/message_repository.py
from sqlalchemy.orm import Session
import uuid
import time
from app.core.database import MessageORM

class MessageRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_message(self, chat_id: str, sender_id: str, content: str, sticker_id: str = None, is_sticker: bool = False) -> MessageORM:
        message = MessageORM(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            sender_id=sender_id,
            content=content,
            sticker_id=sticker_id,
            is_sticker=is_sticker,
            created_at=int(time.time()),
            is_read=False
        )
        self.db.add(message)
        self.db.flush()
        
        print(f"📝 Message created and flushed: {message.id}")
        print(f"   Chat: {chat_id}, Sender: {sender_id}")
        
        return message

    def get_chat_messages(self, chat_id: str, limit: int = None, offset: int = 0) -> list[MessageORM]:
        query = self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id
        ).order_by(MessageORM.created_at.asc())
        
        if limit is not None:
            query = query.offset(offset).limit(limit)
        
        messages = query.all()
        print(f"📚 Loaded {len(messages)} messages from chat {chat_id} (limit={limit})")
        return messages

    def mark_as_read(self, message_id: str, user_id: str) -> MessageORM | None:
        message = self.db.query(MessageORM).filter(
            MessageORM.id == message_id,
            MessageORM.sender_id != user_id
        ).first()
        
        if message and not message.is_read:
            message.is_read = True
            self.db.flush()
            print(f"✓ Message {message_id} marked as read by {user_id}")
            return message
        
        return None

    def get_last_message(self, chat_id: str) -> MessageORM | None:
        return self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id
        ).order_by(MessageORM.created_at.desc()).first()