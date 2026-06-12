# app/repositories/message_repository.py
from sqlalchemy.orm import Session
import uuid
import time
from app.core.database import MessageORM

class MessageRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_message(self,
        chat_id: str,
        sender_id: str,
        content: str,
        sticker_id: str = None,
        is_sticker: bool = False,
        is_image: bool = False,
        reply_to_id: str = None
    ) -> MessageORM:
        message = MessageORM(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            sender_id=sender_id,
            content=content,
            sticker_id=sticker_id,
            is_sticker=is_sticker,
            is_image=is_image,
            reply_to_id=reply_to_id,
            created_at=int(time.time()),
            is_read=False
        )
        print(f"📸 REPO: is_image={is_image}, reply_to_id={reply_to_id}, content={content[:50] if content else None}")
        self.db.add(message)
        self.db.flush()
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
    
    def get_unread_count(self, chat_id: str, user_id: str) -> int:
        return self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id,
            MessageORM.sender_id != user_id,
            MessageORM.is_read == False
        ).count()

    def mark_all_as_read(self, chat_id: str, user_id: str) -> int:
        result = self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id,
            MessageORM.sender_id != user_id,
            MessageORM.is_read == False
        ).update({"is_read": True}, synchronize_session=False)
        self.db.flush()
        print(f"📖 [REPO] Marked {result} messages as read in chat {chat_id} for user {user_id}")
        return result

    def delete_by_chat(self, chat_id: str) -> None:
        self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id
        ).delete(synchronize_session=False)

    def get_message(self, message_id: str) -> MessageORM | None:
        return self.db.query(MessageORM).filter(MessageORM.id == message_id).first()