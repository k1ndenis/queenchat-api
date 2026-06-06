from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.repositories.auth_repository import AuthRepository
from app.models.user import UserSchema
from app.models.auth import TokenResponse, RegisterRequest, LoginRequest
from app.core.security import hash_password, create_token, verify_password
from app.core.redis import redis_cache

class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = AuthRepository(db)

    def get_all_users(self) -> list[UserSchema]:
        cached = redis_cache.get("all_users")
        if cached:
            return [UserSchema(**user) for user in cached]
        
        users_orm = self.repository.get_all_users()
        result = [
            UserSchema(
                id=user_orm.id,
                username=user_orm.username,
                email=user_orm.email,
                created_at=user_orm.created_at
            ) for user_orm in users_orm
        ]
        
        redis_cache.set("all_users", [user.model_dump() for user in result])
        
        return result

    def register(self, payload: RegisterRequest) -> TokenResponse:
        if self.repository.get_by_email(payload.email):
            raise HTTPException(status_code=400, detail="Email already registered")
        
        if self.repository.get_by_username(payload.username):
            raise HTTPException(status_code=400, detail="Username already taken")
        
        hashed_password = hash_password(payload.password)
        user = self.repository.create_user(payload.username, payload.email, hashed_password)
        
        redis_cache.delete("all_users")
        
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
        result = self.repository.delete_user(user_id)
        redis_cache.delete("all_users")
        return result