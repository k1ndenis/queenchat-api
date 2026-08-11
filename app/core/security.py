import os
import jwt
import bcrypt
from datetime import datetime, timedelta, timezone

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "secret-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7
WS_TOKEN_EXPIRE_SECONDS = 300

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_token(user_id: str, username: str, *, scope: str = "access", expires_in: int | None = None) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "scope": scope,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def create_ws_token(user_id: str, username: str) -> str:
    return create_token(user_id, username, scope="websocket", expires_in=WS_TOKEN_EXPIRE_SECONDS)
