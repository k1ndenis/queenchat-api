from sqlalchemy.orm import Session
from app.repositories.chat_repository import ChatRepository
from app.models.chat import ChatResponse, ParticipantResponse
import time

from app.repositories.auth_repository import AuthRepository
from app.models.chat import ChatResponse

class ChatService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = ChatRepository(db)
        self.auth_repo = AuthRepository(db)

    def create_chat(self, name: str, is_group: bool, created_by: str, participant_ids: list[str]):
        chat = self.repo.create_chat(name, is_group, created_by)
        
        all_participants = list(set(participant_ids + [created_by]))
        for user_id in all_participants:
            self.repo.add_participant(chat.id, user_id)
        
        participants = []
        for user_id in all_participants:
            user = self.auth_repo.get_by_id(user_id)
            participants.append({
                "user_id": user_id,
                "username": user.username,
                "joined_at": int(time.time())
            })
        
        return ChatResponse(
            id=chat.id,
            name=chat.name,
            is_group=chat.is_group,
            created_by=chat.created_by,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            participants=participants
        )

    def is_participant(self, chat_id: str, user_id: str) -> bool:
        return self.repo.is_participant(chat_id, user_id)

    def get_user_chats(self, user_id: str):
        chats = self.repo.get_user_chats(user_id)
        result = []
        for chat in chats:
            participants = []
            for participant in chat.participants:
                participants.append(ParticipantResponse(
                    user_id=participant.id,
                    username=participant.username,
                    joined_at=chat.created_at
                ))
            result.append(ChatResponse(
                id=chat.id,
                name=chat.name,
                is_group=chat.is_group,
                created_by=chat.created_by,
                created_at=chat.created_at,
                updated_at=chat.updated_at,
                participants=participants
            ))
        return result

    def get_chat(self, chat_id: str) -> ChatResponse | None:
        chat = self.repo.get_chat(chat_id)
        if not chat:
            return None
        
        participants = []
        # chat.participants — это список UserORM
        for user in chat.participants:
            participants.append({
                "user_id": user.id,
                "username": user.username,
                "joined_at": chat.created_at  # временно
            })
        
        return ChatResponse(
            id=chat.id,
            name=chat.name,
            is_group=chat.is_group,
            created_by=chat.created_by,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            participants=participants
        )

    def add_participant(self, chat_id: str, user_id: str):
        return self.repo.add_participant(chat_id, user_id)

    def remove_participant(self, chat_id: str, user_id: str):
        return self.repo.remove_participant(chat_id, user_id)