from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Optional
from pydantic import BaseModel
import os
import redis
import firebase_admin
from firebase_admin import credentials, messaging

from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User

router = APIRouter()

TESTING = os.getenv("TESTING", "false").lower() == "true"

REDIS_URL = os.getenv("REDIS_URL")
redis_client = None
REDIS_AVAILABLE = False

if not TESTING:
    try:
        redis_client = redis.from_url(REDIS_URL)
        REDIS_AVAILABLE = True
        print("✅ Redis connected for FCM tokens")
    except Exception as e:
        print(f"⚠️ Redis not available: {e}")

fcm_tokens: Dict[str, str] = {}


class FCMToken(BaseModel):
    token: str


def save_fcm_token(user_id: str, token: str):
    if REDIS_AVAILABLE:
        redis_client.setex(f"fcm:{user_id}", 60 * 60 * 24 * 30, token)
    else:
        fcm_tokens[user_id] = token


def get_fcm_token(user_id: str) -> str | None:
    if TESTING:
        return None
    if REDIS_AVAILABLE:
        token = redis_client.get(f"fcm:{user_id}")
        return token.decode() if token else None
    return fcm_tokens.get(user_id)


def delete_fcm_token(user_id: str):
    if REDIS_AVAILABLE:
        redis_client.delete(f"fcm:{user_id}")
    else:
        fcm_tokens.pop(user_id, None)


@router.post("/fcm-token")
def save_token(
    token_data: FCMToken,
    current_user: User = Depends(get_current_user),
):
    print(f"📝 Saving FCM token for user {current_user.id}")
    save_fcm_token(str(current_user.id), token_data.token)
    return {"status": "ok"}


@router.delete("/fcm-token")
def remove_token(
    current_user: User = Depends(get_current_user),
):
    print(f"🗑️ Removing FCM token for user {current_user.id}")
    delete_fcm_token(str(current_user.id))
    return {"status": "ok"}


@router.get("/fcm-status")
def fcm_status(current_user: User = Depends(get_current_user)):
    token = get_fcm_token(str(current_user.id))
    return {
        "subscribed": token is not None,
        "fcm_available": True,
        "token_preview": token[:20] + "..." if token else None
    }


async def send_fcm_notification(
    user_id: str,
    title: str,
    body: str,
    url: str = "/chat"
):
    token = get_fcm_token(str(user_id))
    
    if not token:
        print(f"⚠️ No FCM token for user {user_id}")
        return False
    
    try:
        message = messaging.Message(
            token=token,
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data={
                "title": title,
                "body": body,
                "url": url,
            },
            webpush=messaging.WebpushConfig(
                notification=messaging.WebpushNotification(
                    icon="/favicon.ico",
                    badge="/favicon-96x96.png"
                )
            ),
        )

        response = messaging.send(message)
        print(f"✅ FCM sent to user {user_id}: {response}")
        return True

    except Exception as e:
        print(f"❌ FCM error for user {user_id}: {e}")
        return False