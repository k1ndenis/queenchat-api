from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.repositories.auth_repository import AuthRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.notification_repository import NotificationRepository
from app.models.user import UserSchema
from app.models.auth import TokenResponse, RegisterRequest, LoginRequest
from app.core.security import hash_password, create_token, verify_password
from app.core.redis import redis_cache
from app.core.database import UserORM

class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = AuthRepository(db)
        self.chat_repo = ChatRepository(db)
        self.message_repo = MessageRepository(db)
        self.notification_repo = NotificationRepository(db)

    def get_user_profile(self, user_id: str) -> UserSchema | None:
        cached = redis_cache.get(f"user_profile:{user_id}")
        if cached:
            return UserSchema(**cached)
        
        user_orm = self.repository.get_by_id(user_id)
        if not user_orm:
            return None
        
        result = UserSchema(
            id=user_orm.id,
            username=user_orm.username,
            email=user_orm.email,
            avatar=user_orm.avatar,
            created_at=user_orm.created_at
        )
        
        redis_cache.set(f"user_profile:{user_id}", result.model_dump(), ttl=300)
        
        return result

    def get_all_users(self, exclude_current: bool = True, current_user_id: str = None) -> list[UserSchema]:
        cache_key = "all_users"
        if exclude_current and current_user_id:
            cache_key = f"all_users_exclude_{current_user_id}"
        
        cached = redis_cache.get(cache_key)
        if cached:
            return [UserSchema(**user) for user in cached]
        
        users_orm = self.repository.get_all_users(
            exclude_user_id=current_user_id if exclude_current else None
        )
        
        result = [
            UserSchema(
                id=user_orm.id,
                username=user_orm.username,
                email=user_orm.email,
                avatar=user_orm.avatar,
                created_at=user_orm.created_at
            ) for user_orm in users_orm
        ]
        
        redis_cache.set(cache_key, [user.model_dump() for user in result], ttl=300)
        
        return result

    def register(self, payload: RegisterRequest) -> TokenResponse:
        if self.repository.get_by_email(payload.email):
            raise HTTPException(status_code=400, detail="Email already registered")
        
        if self.repository.get_by_username(payload.username):
            raise HTTPException(status_code=400, detail="Username already taken")
        
        hashed_password = hash_password(payload.password)
        user = self.repository.create_user(payload.username, payload.email, hashed_password)
        
        redis_cache.delete("all_users")
        redis_cache.delete_pattern("all_users_exclude_*")
        
        token = create_token(user.id, user.username)
        
        return {
            "token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email
            }
        }
    
    def login(self, payload: LoginRequest) -> TokenResponse:
        user = self.repository.get_by_email(payload.email)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        if not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        token = create_token(user.id, user.username)
        
        return {
            "token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email
            }
        }
    
    def delete_user(self, user_id: str) -> bool:
        try:
            user_chats = self.chat_repo.get_user_chats(user_id)
            
            for chat in user_chats:
                self.notification_repo.delete_by_chat(chat.id)
                self.message_repo.delete_by_chat(chat.id)
                self.chat_repo.delete_participants(chat.id)
                self.chat_repo.delete_chat(chat.id)
            
            redis_cache.delete("all_users")
            redis_cache.delete_pattern("all_users_exclude_*")
            redis_cache.delete(f"user_profile:{user_id}")
            redis_cache.delete(f"user:{user_id}")
            redis_cache.delete(f"user_chats:{user_id}")
            redis_cache.delete(f"notifications:{user_id}")
            
            result = self.repository.delete_user(user_id)
            
            self.db.commit()
            return result
            
        except Exception as e:
            self.db.rollback()
            print(f"Error deleting user {user_id}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def update_profile(self, user_id: str, username: str, email: str, avatar: str = None) -> UserORM | None:
        user = self.repository.update_user(user_id, username, email, avatar)
        
        if user:
            redis_cache.delete(f"user_profile:{user_id}")
            redis_cache.delete("all_users")
            redis_cache.delete_pattern("all_users_exclude_*")
        
        return user