from fastapi import WebSocket, Depends
from sqlalchemy.orm import Session
import jwt
import os
import logging

from app.core.dependency import get_db

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"
logger = logging.getLogger(__name__)
MAX_WS_PER_USER = int(os.getenv("MAX_WS_PER_USER", "8"))


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, dict[str, set[WebSocket]]] = {}
        self.global_connections: dict[str, set[WebSocket]] = {}

    async def connect(self, chat_id: str, user_id: str, websocket: WebSocket):
        # Убираем await websocket.accept() - оно уже вызвано в get_current_user_ws
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = {}
        if user_id not in self.active_connections[chat_id]:
            self.active_connections[chat_id][user_id] = set()
        self.active_connections[chat_id][user_id].add(websocket)

    def connection_count(self, user_id: str) -> int:
        return len(self.global_connections.get(user_id, set())) + sum(
            len(users.get(user_id, set())) for users in self.active_connections.values()
        )

    def disconnect(self, chat_id: str, user_id: str, websocket: WebSocket | None = None):
        if chat_id in self.active_connections:
            if user_id in self.active_connections[chat_id]:
                if websocket is None:
                    self.active_connections[chat_id].pop(user_id, None)
                else:
                    self.active_connections[chat_id][user_id].discard(websocket)
                    if not self.active_connections[chat_id][user_id]:
                        self.active_connections[chat_id].pop(user_id, None)
            if not self.active_connections[chat_id]:
                del self.active_connections[chat_id]

    async def send_personal_message(self, message: dict, chat_id: str, user_id: str):
        if chat_id in self.active_connections:
            websockets = list(self.active_connections[chat_id].get(user_id, set()))
            for websocket in websockets:
                try:
                    await websocket.send_json(message)
                except Exception as e:
                    logger.warning(
                        "[WSHealth] stale connection removed: user_id=%s chat_id=%s error_type=%s",
                        user_id,
                        chat_id,
                        type(e).__name__,
                    )
                    self.disconnect(chat_id, user_id, websocket)

    async def broadcast_to_chat(self, message: dict, chat_id: str, exclude_user_id: str = None):
        if chat_id in self.active_connections:
            for user_id, websockets in list(self.active_connections[chat_id].items()):
                if user_id != exclude_user_id:
                    for websocket in list(websockets):
                        try:
                            await websocket.send_json(message)
                        except Exception as e:
                            logger.warning(
                                "[WSHealth] stale connection removed: user_id=%s chat_id=%s error_type=%s",
                                user_id,
                                chat_id,
                                type(e).__name__,
                            )
                            self.disconnect(chat_id, user_id, websocket)

    async def connect_global(self, user_id: str, websocket: WebSocket):
        """Сохраняет глобальное WebSocket соединение пользователя"""
        if user_id not in self.global_connections:
            self.global_connections[user_id] = set()
        self.global_connections[user_id].add(websocket)

    def disconnect_global(self, user_id: str, websocket: WebSocket | None = None):
        """Удаляет глобальное WebSocket соединение"""
        if user_id in self.global_connections:
            if websocket is None:
                del self.global_connections[user_id]
            else:
                self.global_connections[user_id].discard(websocket)
                if not self.global_connections[user_id]:
                    del self.global_connections[user_id]

    async def send_global_message(self, user_id: str, message: dict):
        sent = False
        for websocket in list(self.global_connections.get(user_id, set())):
            try:
                await websocket.send_json(message)
                sent = True
            except Exception as e:
                logger.warning(
                    "[WSHealth] stale global connection removed: user_id=%s signal_type=%s call_id=%s error_type=%s",
                    user_id,
                    message.get("signal_type"),
                    message.get("call_id"),
                    type(e).__name__,
                )
                self.disconnect_global(user_id, websocket)
        return sent

    async def close_user_connections(self, user_id: str, reason: str = "Account unavailable"):
        """Close only this user's sockets; never affect other Redis/WS users."""
        sockets = list(self.global_connections.get(user_id, set()))
        for users in self.active_connections.values():
            sockets.extend(users.get(user_id, set()))
        for websocket in sockets:
            try:
                await websocket.close(code=4003, reason=reason)
            except Exception:
                pass
        self.disconnect_global(user_id)
        for chat_id in list(self.active_connections):
            self.disconnect(chat_id, user_id)


manager = ConnectionManager()


async def get_current_user_ws(
    websocket: WebSocket,
    token: str,
    db: Session = Depends(get_db)
):
    # Принимаем соединение здесь, а не в manager.connect()
    await websocket.accept()
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("scope") != "websocket":
            await websocket.close(code=4004, reason="Invalid token")
            return None
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
        if user.is_blocked:
            await websocket.close(code=4003, reason="Account is blocked")
            return None
        return user
    except jwt.ExpiredSignatureError:
        await websocket.close(code=4003, reason="Token expired")
        return None
    except jwt.InvalidTokenError:
        await websocket.close(code=4004, reason="Invalid token")
        return None
