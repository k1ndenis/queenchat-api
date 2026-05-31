from sqlalchemy.orm import Session
import uuid
import time
from app.core.database import ChatORM, ChatParticipantORM

class ChatRepository:
    def __init__(self, db: Session):
        self.db = db

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