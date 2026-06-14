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

    def create_chat(self, name: str, is_group: bool, created_by: str, chat_type: str) -> ChatORM:
        chat = ChatORM(
            id=str(uuid.uuid4()),
            name=name,
            is_group=is_group,
            chat_type=chat_type,
            created_by=created_by,
            created_at=int(time.time()),
            updated_at=int(time.time())
        )
        self.db.add(chat)
        self.db.flush()
        return chat

    def delete_chat(self, chat_id: str) -> bool:
        try:
            from app.core.database import NotificationORM
            self.db.query(NotificationORM).filter(
                NotificationORM.chat_id == chat_id
            ).delete(synchronize_session=False)
            
            self.db.query(ChatParticipantORM).filter(
                ChatParticipantORM.chat_id == chat_id
            ).delete(synchronize_session=False)
            
            self.db.query(MessageORM).filter(
                MessageORM.chat_id == chat_id
            ).delete(synchronize_session=False)
            
            self.db.query(ChatORM).filter(ChatORM.id == chat_id).delete(synchronize_session=False)
            
            self.db.commit()
            print(f"Chat {chat_id} deleted successfully with all related data")
            return True
            
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
        self.db.flush()
        return participant

    def is_participant(self, chat_id: str, user_id: str) -> bool:
        return self.db.query(ChatParticipantORM).filter(
            ChatParticipantORM.chat_id == chat_id,
            ChatParticipantORM.user_id == user_id
        ).first() is not None

    def delete_participants(self, chat_id: str) -> None:
        self.db.query(ChatParticipantORM).filter(
            ChatParticipantORM.chat_id == chat_id
        ).delete(synchronize_session=False)

    def get_user_chats(self, user_id: str) -> list[ChatORM]:
        return self.db.query(ChatORM).join(
            ChatParticipantORM
        ).filter(
            ChatParticipantORM.user_id == user_id
        ).all()

    def get_chat(self, chat_id: str) -> ChatORM | None:
        return self.db.query(ChatORM).filter(ChatORM.id == chat_id).first()

    def update_chat(self, chat_id: str, name: str = None, avatar: str = None) -> ChatORM | None:
        chat = self.get_chat(chat_id)
        if not chat:
            return None
        
        if name is not None:
            chat.name = name
        if avatar is not None:
            chat.avatar = avatar
        
        chat.updated_at = int(time.time())
        self.db.add(chat)
        self.db.flush()
        return chat

    def remove_participant(self, chat_id: str, user_id: str) -> bool:
        try:
            participant = self.db.query(ChatParticipantORM).filter(
                ChatParticipantORM.chat_id == chat_id,
                ChatParticipantORM.user_id == user_id
            ).first()
            
            if not participant:
                print(f"❌ Participant {user_id} not found in chat {chat_id}")
                return False
            
            self.db.delete(participant)
            self.db.flush()
            print(f"✅ Participant {user_id} removed from chat {chat_id}")
            return True
        except Exception as e:
            print(f"❌ Error removing participant: {e}")
            self.db.rollback()
            return False