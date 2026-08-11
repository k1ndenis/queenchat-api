from fastapi import APIRouter, Depends, Query
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
import json
import os
import time
import uuid
import redis
import logging
from firebase_admin import messaging

from app.core.dependency import get_current_user
from app.core.database import UserORM as User

router = APIRouter()
logger = logging.getLogger(__name__)

TESTING = os.getenv("TESTING", "false").lower() == "true"
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 90
DEFAULT_WEBPUSH_ORIGIN = "https://queenchat.ru"
CURRENT_FCM_SW_VERSION = "firebase-messaging-sw-v2"
ANDROID_CAPACITOR_FCM_VERSION = "android_capacitor"
LEGACY_FCM_SW_VERSION = "legacy"

REDIS_URL = os.getenv("REDIS_URL")
redis_client = None
REDIS_AVAILABLE = False

if not TESTING:
    try:
        redis_client = redis.from_url(REDIS_URL) if REDIS_URL else None
        if redis_client:
            redis_client.ping()
            REDIS_AVAILABLE = True
            logger.info("Redis connected for FCM tokens")
    except Exception as e:
        logger.warning("Redis not available for FCM tokens: error_type=%s", type(e).__name__)

fcm_tokens: Dict[str, Dict[str, Dict[str, Any]]] = {}


class NotificationSettings(BaseModel):
    enabled: bool = True
    messages: bool = True
    direct_messages: bool = True
    groups: bool = True
    channels: bool = True
    calls: bool = True
    reactions: bool = True
    comments: bool = True
    preview_text: bool = True
    sound: bool = True
    vibration: bool = True
    do_not_disturb_until: Optional[int] = None
    muted_until: Optional[int] = None
    chat_overrides: Dict[str, Any] = Field(default_factory=dict)


class FCMToken(BaseModel):
    token: str = Field(min_length=8, max_length=4096)
    device_id: Optional[str] = Field(default=None, max_length=128)
    platform: Optional[str] = Field(default=None, max_length=512)
    permission: Optional[str] = Field(default=None, max_length=32)
    sw_version: Optional[str] = Field(default=None, max_length=64)
    settings: Optional[Dict[str, Any]] = None


def _user_key(user_id: str) -> str:
    return f"fcm:{user_id}"


def _device_id(device_id: Optional[str], token: str) -> str:
    return device_id or str(abs(hash(token)))


def _serialize(record: Dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def _deserialize(value: bytes | str) -> Dict[str, Any]:
    raw = value.decode() if isinstance(value, bytes) else value
    return json.loads(raw)


def _is_wrong_type_error(exc: Exception) -> bool:
    return "WRONGTYPE" in str(exc)


def _read_legacy_token(user_id: str) -> Optional[str]:
    if not (REDIS_AVAILABLE and redis_client):
        return None
    try:
        token = redis_client.get(_user_key(user_id))
        return token.decode() if isinstance(token, bytes) else token
    except Exception as e:
        logger.warning(
            "Failed to read legacy FCM token: user_id=%s error_type=%s",
            user_id,
            type(e).__name__,
        )
        return None


def save_fcm_token(
    user_id: str,
    token: str,
    device_id: Optional[str] = None,
    platform: Optional[str] = None,
    permission: Optional[str] = None,
    sw_version: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
):
    device = _device_id(device_id, token)
    version = sw_version or LEGACY_FCM_SW_VERSION
    record = {
        "token": token,
        "device_id": device,
        "platform": platform,
        "permission": permission,
        "sw_version": version,
        "settings": settings or {},
        "updated_at": int(time.time()),
    }

    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.hset(_user_key(user_id), device, _serialize(record))
            _delete_tokens_with_other_sw_version(user_id, version, keep_device_id=device)
            redis_client.expire(_user_key(user_id), TOKEN_TTL_SECONDS)
        except redis.exceptions.ResponseError as e:
            if not _is_wrong_type_error(e):
                raise
            redis_client.delete(_user_key(user_id))
            redis_client.hset(_user_key(user_id), device, _serialize(record))
            redis_client.expire(_user_key(user_id), TOKEN_TTL_SECONDS)
    else:
        fcm_tokens.setdefault(user_id, {})[device] = record
        _delete_memory_tokens_with_other_sw_version(user_id, version, keep_device_id=device)


def _delete_tokens_with_other_sw_version(user_id: str, sw_version: str, keep_device_id: str):
    if not (REDIS_AVAILABLE and redis_client):
        return
    if sw_version == ANDROID_CAPACITOR_FCM_VERSION:
        return
    removed = 0
    try:
        for raw_device, raw_value in list(redis_client.hgetall(_user_key(user_id)).items()):
            device = raw_device.decode() if isinstance(raw_device, bytes) else raw_device
            if device == keep_device_id:
                continue
            try:
                record = _deserialize(raw_value)
            except Exception:
                redis_client.hdel(_user_key(user_id), device)
                removed += 1
                continue
            if record.get("sw_version") not in {sw_version, ANDROID_CAPACITOR_FCM_VERSION}:
                redis_client.hdel(_user_key(user_id), device)
                removed += 1
        if removed:
            logger.info(
                "Removed stale FCM tokens for user_id=%s sw_version=%s removed_count=%s",
                user_id,
                sw_version,
                removed,
            )
    except redis.exceptions.ResponseError as e:
        if not _is_wrong_type_error(e):
            raise


def _delete_memory_tokens_with_other_sw_version(user_id: str, sw_version: str, keep_device_id: str):
    user_tokens = fcm_tokens.get(user_id)
    if not isinstance(user_tokens, dict):
        return
    if sw_version == ANDROID_CAPACITOR_FCM_VERSION:
        return
    for device, record in list(user_tokens.items()):
        if device != keep_device_id and record.get("sw_version") not in {sw_version, ANDROID_CAPACITOR_FCM_VERSION}:
            user_tokens.pop(device, None)


def get_fcm_token(user_id: str) -> str | None:
    tokens = get_fcm_tokens(user_id)
    return tokens[0]["token"] if tokens else None


def get_fcm_tokens(user_id: str) -> list[Dict[str, Any]]:
    if TESTING:
        return []
    if REDIS_AVAILABLE and redis_client:
        try:
            values = redis_client.hvals(_user_key(user_id))
            return [_deserialize(value) for value in values]
        except redis.exceptions.ResponseError as e:
            if not _is_wrong_type_error(e):
                raise
            legacy_token = _read_legacy_token(user_id)
            return [{"token": legacy_token, "device_id": "legacy", "settings": {}}] if legacy_token else []
    user_tokens = fcm_tokens.get(user_id, {})
    if isinstance(user_tokens, str):
        return [{"token": user_tokens, "device_id": "legacy", "settings": {}}]
    return list(user_tokens.values())


def delete_fcm_token(user_id: str, device_id: Optional[str] = None, token: Optional[str] = None):
    if REDIS_AVAILABLE and redis_client:
        try:
            if device_id:
                redis_client.hdel(_user_key(user_id), device_id)
                return
            if token:
                for device, value in redis_client.hgetall(_user_key(user_id)).items():
                    record = _deserialize(value)
                    if record.get("token") == token:
                        redis_client.hdel(_user_key(user_id), device)
                return
            redis_client.delete(_user_key(user_id))
            return
        except redis.exceptions.ResponseError as e:
            if not _is_wrong_type_error(e):
                raise
            if device_id or token:
                legacy_token = _read_legacy_token(user_id)
                if legacy_token and (token is None or legacy_token == token):
                    redis_client.delete(_user_key(user_id))
            else:
                redis_client.delete(_user_key(user_id))
            return

    if user_id not in fcm_tokens:
        return
    if device_id:
        fcm_tokens[user_id].pop(device_id, None)
    elif token:
        for device, record in list(fcm_tokens[user_id].items()):
            if record.get("token") == token:
                fcm_tokens[user_id].pop(device, None)
    else:
        fcm_tokens.pop(user_id, None)


def update_fcm_settings(user_id: str, device_id: str, settings: Dict[str, Any]):
    records = get_fcm_tokens(user_id)
    for record in records:
        if record.get("device_id") == device_id:
            save_fcm_token(
                user_id=user_id,
                token=record["token"],
                device_id=device_id,
                platform=record.get("platform"),
                permission=record.get("permission"),
                sw_version=record.get("sw_version"),
                settings=settings,
            )
            return True
    return False


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _string_data(data: Dict[str, Any]) -> Dict[str, str]:
    result = {}
    for key, value in data.items():
        if value is None:
            continue
        result[key] = value if isinstance(value, str) else str(value)
    return result


def _webpush_topic(value: Optional[str]) -> str:
    raw = value or "queenchat"
    return "".join(ch for ch in raw if ch.isalnum() or ch in "_-")[:32] or "queenchat"


def _webpush_link(url: str) -> str:
    if url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{DEFAULT_WEBPUSH_ORIGIN}{url}"
    return f"{DEFAULT_WEBPUSH_ORIGIN}/{url.lstrip('/')}"


def _is_invalid_token_error(exc: Exception) -> bool:
    code = getattr(exc, "code", "") or getattr(exc, "error_code", "")
    text = str(exc).lower()
    return (
        "registration-token-not-registered" in str(code)
        or "invalid-registration-token" in str(code)
        or "not registered" in text
        or "invalid registration" in text
        or exc.__class__.__name__ in {"UnregisteredError", "InvalidArgumentError"}
    )


def _setting(settings: Dict[str, Any], snake_key: str, camel_key: str, default: Any = True) -> Any:
    if snake_key in settings:
        return settings[snake_key]
    if camel_key in settings:
        return settings[camel_key]
    return default


def _settings_allow(record: Dict[str, Any], event_type: str, chat_id: Optional[str], chat_type: Optional[str]) -> bool:
    settings = record.get("settings") or {}
    now_ms = int(time.time() * 1000)

    if _setting(settings, "enabled", "enabled", True) is False:
        return False
    muted_until = _setting(settings, "muted_until", "mutedUntil", None)
    dnd_until = _setting(settings, "do_not_disturb_until", "doNotDisturbUntil", None)
    if muted_until and int(muted_until) > now_ms:
        return False
    if dnd_until and int(dnd_until) > now_ms:
        return False

    overrides = _setting(settings, "chat_overrides", "chatOverrides", {}) or {}
    if chat_id and chat_id in overrides:
        override = overrides[chat_id] or {}
        override_until = override.get("mutedUntil") or override.get("muted_until")
        if override.get("muted") and (not override_until or int(override_until) > now_ms):
            return False

    if event_type.startswith("call") or event_type == "incoming_call":
        return _setting(settings, "calls", "calls", True) is not False

    if event_type == "message_reaction":
        return _setting(settings, "reactions", "reactions", True) is not False
    if event_type == "message_comment":
        return _setting(settings, "comments", "comments", True) is not False

    if event_type in {"message", "reply", "mention"}:
        if _setting(settings, "messages", "messages", True) is False:
            return False
        if chat_type == "private" and _setting(settings, "direct_messages", "directMessages", True) is False:
            return False
        if chat_type == "group" and _setting(settings, "groups", "groups", True) is False:
            return False
        if chat_type == "channel" and _setting(settings, "channels", "channels", True) is False:
            return False

    return True


def _preview_enabled(record: Dict[str, Any]) -> bool:
    settings = record.get("settings") or {}
    return _setting(settings, "preview_text", "previewText", True) is not False


def _log_token_diagnostics(event: str, user_id: str, record: Dict[str, Any], device_count: int):
    token = record.get("token") or ""
    logger.warning(
        "%s: user_id=%s device_id=%s platform=%s sw_version=%s token_len=%s updated_at=%s device_count=%s",
        event,
        user_id,
        record.get("device_id"),
        record.get("platform"),
        record.get("sw_version"),
        len(token),
        record.get("updated_at"),
        device_count,
    )


@router.post("/fcm-token")
def save_token(
    token_data: FCMToken,
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    save_fcm_token(
        user_id,
        token_data.token,
        device_id=token_data.device_id,
        platform=token_data.platform,
        permission=token_data.permission,
        sw_version=token_data.sw_version,
        settings=token_data.settings,
    )
    tokens = get_fcm_tokens(user_id)
    for record in tokens:
        if record.get("device_id") == (token_data.device_id or _device_id(None, token_data.token)):
            _log_token_diagnostics("FCM token registered", user_id, record, len(tokens))
            break
    return {"status": "ok"}


@router.delete("/fcm-token")
def remove_token(
    device_id: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    delete_fcm_token(str(current_user.id), device_id=device_id)
    return {"status": "ok"}


@router.put("/fcm-settings")
def save_settings(
    settings: NotificationSettings,
    current_user: User = Depends(get_current_user),
    device_id: str = Query(default=""),
):
    payload = settings.model_dump()
    if device_id:
        update_fcm_settings(str(current_user.id), device_id, payload)
    return {"status": "ok"}


@router.get("/fcm-status")
def fcm_status(current_user: User = Depends(get_current_user)):
    tokens = get_fcm_tokens(str(current_user.id))
    return {
        "subscribed": len(tokens) > 0,
        "fcm_available": True,
        "device_count": len(tokens),
    }


async def send_fcm_notification(
    user_id: str,
    title: str,
    body: str,
    url: str = "/chat",
    *,
    event_type: str = "message",
    chat_id: Optional[str] = None,
    chat_type: Optional[str] = None,
    message_id: Optional[str] = None,
    sender_id: Optional[str] = None,
    sender_name: Optional[str] = None,
    avatar: Optional[str] = None,
    chat_avatar: Optional[str] = None,
    image: Optional[str] = None,
    unread_count: Optional[int] = None,
    collapse_id: Optional[str] = None,
    require_interaction: bool = False,
    call_id: Optional[str] = None,
    caller_id: Optional[str] = None,
    call_type: Optional[str] = None,
    reaction: Optional[str] = None,
    reactor_id: Optional[str] = None,
    reactor_name: Optional[str] = None,
):
    try:
        tokens = get_fcm_tokens(str(user_id))
        if not tokens:
            legacy_token = get_fcm_token(str(user_id))
            tokens = [{"token": legacy_token, "device_id": "legacy", "settings": {}}] if legacy_token else []
    except Exception as e:
        logger.warning(
            "Failed to load FCM tokens: user_id=%s error_type=%s",
            user_id,
            type(e).__name__,
        )
        return False

    if not tokens:
        logger.info("No FCM token for user_id=%s", user_id)
        return False

    base_data = _string_data({
        "notification_id": str(uuid.uuid4()),
        "event_type": event_type,
        "title": title,
        "body": body,
        "hidden_body": "New message",
        "url": url,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "message_id": message_id,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "caller_id": caller_id,
        "caller_name": sender_name,
        "caller_avatar": avatar,
        "call_id": call_id,
        "call_type": call_type,
        "reaction": reaction,
        "reactor_id": reactor_id,
        "reactor_name": reactor_name,
        "avatar": avatar,
        "chat_avatar": chat_avatar,
        "image": image,
        "badge": "/favicon-96x96.png",
        "icon": avatar or chat_avatar or "/web-app-manifest-192x192.png",
        "tag": collapse_id or (f"chat:{chat_id}" if chat_id else f"user:{user_id}"),
        "renotify": _bool(event_type.startswith("call") or event_type == "incoming_call"),
        "require_interaction": _bool(require_interaction or event_type.startswith("call") or event_type == "incoming_call"),
        "unread_count": unread_count,
        "created_at": int(time.time() * 1000),
    })

    success_count = 0
    for record in tokens:
        token = record.get("token")
        if not token:
            continue
        if not _settings_allow(record, event_type, chat_id, chat_type):
            logger.info(
                "[PushDecision] FCM send skipped: user_id=%s device_id=%s reason=notification_policy",
                user_id,
                record.get("device_id"),
            )
            continue
        data = dict(base_data)
        if not _preview_enabled(record):
            data["body"] = data.get("hidden_body", "New message")
        try:
            message = messaging.Message(
                token=token,
                data=data,
                android=(
                    messaging.AndroidConfig(priority="high")
                    if record.get("sw_version") == ANDROID_CAPACITOR_FCM_VERSION
                    else None
                ),
                webpush=messaging.WebpushConfig(
                    headers={
                        "TTL": "2419200",
                        "Urgency": "high" if event_type.startswith("call") or event_type == "incoming_call" else "normal",
                        "Topic": _webpush_topic(collapse_id or chat_id or event_type),
                    },
                    fcm_options=messaging.WebpushFCMOptions(link=_webpush_link(url)),
                ),
            )

            messaging.send(message)
            success_count += 1
            _log_token_diagnostics("FCM send succeeded", str(user_id), record, len(tokens))
        except Exception as e:
            logger.warning(
                "FCM send failed: user_id=%s device_id=%s error_type=%s error=%s",
                user_id,
                record.get("device_id"),
                type(e).__name__,
                str(e),
            )
            if _is_invalid_token_error(e):
                try:
                    delete_fcm_token(str(user_id), device_id=record.get("device_id"), token=token)
                except Exception as delete_error:
                    logger.warning(
                        "Failed to delete invalid FCM token: user_id=%s device_id=%s error_type=%s",
                        user_id,
                        record.get("device_id"),
                        type(delete_error).__name__,
                    )

    return success_count > 0
