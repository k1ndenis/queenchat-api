from app.core.websocket import manager
from app.core.redis import redis_cache
import asyncio
import requests

class Notifier:
    @staticmethod
    async def send_websocket(user_id: str, data: dict):
        await manager.send_notification(user_id, data)
    
    @staticmethod
    def send_push(user_id: str, title: str, body: str, chat_id: str = None):
        token = redis_cache.get(f"push_token:{user_id}")
        if not token:
            return False
        
        return True
    
    @staticmethod
    async def notify_new_message(user_id: str, chat_id: str, title: str, message: str, data: dict):
        asyncio.create_task(Notifier.send_websocket(user_id, data))
        Notifier.send_push(user_id, title, message, chat_id)