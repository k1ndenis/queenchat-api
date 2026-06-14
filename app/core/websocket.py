from fastapi import WebSocket, Depends
from sqlalchemy.orm import Session
import jwt
import os

from app.core.dependency import get_db

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, dict[str, WebSocket]] = {}

    async def connect(self, chat_id: str, user_id: str, websocket: WebSocket):
        await websocket.accept()
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = {}
        self.active_connections[chat_id][user_id] = websocket

    def disconnect(self, chat_id: str, user_id: str):
        if chat_id in self.active_connections:
            self.active_connections[chat_id].pop(user_id, None)
            if not self.active_connections[chat_id]:
                del self.active_connections[chat_id]

    async def send_personal_message(self, message: dict, chat_id: str, user_id: str):
        if chat_id in self.active_connections:
            websocket = self.active_connections[chat_id].get(user_id)
            if websocket:
                await websocket.send_json(message)

    async def broadcast_to_chat(self, message: dict, chat_id: str, exclude_user_id: str = None):
        if chat_id in self.active_connections:
            for user_id, websocket in self.active_connections[chat_id].items():
                if user_id != exclude_user_id:
                    try:
                        await websocket.send_json(message)
                    except:
                        pass


manager = ConnectionManager()


async def get_current_user_ws(
    websocket: WebSocket,
    token: str,
    db: Session = Depends(get_db)
):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            await websocket.close(code=4001, reason="Invalid token")
            return None
        
        from app.repositories.auth_repository import AuthRepository
        repo = AuthRepository(db)
        user = repo.get_by_id(user_id)
        if user is None:
            await websocket.close(code=4002, reason="User not found")
            return None
        return user
    except jwt.ExpiredSignatureError:
        await websocket.close(code=4003, reason="Token expired")
        return None
    except jwt.InvalidTokenError:
        await websocket.close(code=4004, reason="Invalid token")
        return None