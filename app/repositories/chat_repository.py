from sqlalchemy.orm import Session
from sqlalchemy import func
import uuid
import time
from app.core.database import ChatORM, ChatParticipantORM, MessageORM

class ChatRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_existing_private_chat(self, user1_id: str, user2_id: str) -> ChatORM | None:
        chat_ids = self.db.query(ChatParticipantORM.chat_id).filter(
            ChatParticipantORM.user_id.in_([user1_id, user2_id])
        ).group_by(ChatParticipantORM.chat_id).having(
            func.count(ChatParticipantORM.user_id) == 2
        ).all()
        
        if not chat_ids:
            return None
        
        return self.db.query(ChatORM).filter(
            ChatORM.id.in_([c[0] for c in chat_ids]),
            ChatORM.is_group == False
        ).first()

    def create_chat(self, name: str, is_group: bool, created_by: str) -> ChatORM:
        chat = ChatORM(
            id=str(uuid.uuid4()),
            name=name,
            is_group=is_group,
            created_by=created_by,
            created_at=int(time.time()),
            updated_at=int(time.time())
        )
        self.db.add(chat)
        self.db.commit()
        self.db.refresh(chat)
        return chat

    def delete_chat(self, chat_id: str) -> bool:
        try:
            self.db.query(ChatParticipantORM).filter(
                ChatParticipantORM.chat_id == chat_id
            ).delete()
            
            self.db.query(MessageORM).filter(
                MessageORM.chat_id == chat_id
            ).delete()
            
            chat = self.db.query(ChatORM).filter(ChatORM.id == chat_id).first()
            if chat:
                self.db.delete(chat)
                self.db.commit()
                return True
            return False
        except Exception as e:
            self.db.rollback()
            print(f"Error deleting chat: {e}")
            return False

    def add_participant(self, chat_id: str, user_id: str):
        participant = ChatParticipantORM(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            user_id=user_id,
            joined_at=int(time.time())
        )
        self.db.add(participant)
        self.db.commit()
        return participant

    def is_participant(self, chat_id: str, user_id: str) -> bool:
        return self.db.query(ChatParticipantORM).filter(
            ChatParticipantORM.chat_id == chat_id,
            ChatParticipantORM.user_id == user_id
        ).first() is not None

    def get_user_chats(self, user_id: str) -> list[ChatORM]:
        return self.db.query(ChatORM).join(
            ChatParticipantORM
        ).filter(
            ChatParticipantORM.user_id == user_id
        ).all()

    def get_chat(self, chat_id: str) -> ChatORM | None:
        return self.db.query(ChatORM).filter(ChatORM.id == chat_id).first()