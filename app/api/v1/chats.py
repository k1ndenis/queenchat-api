from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
import json
import asyncio
import time
import logging

from app.core.websocket import manager, get_current_user_ws, MAX_WS_PER_USER
from app.core.rate_limit import WS_EVENTS, hit
from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User, MessageCommentORM, ChatBackgroundPreferenceORM
from app.core.redis import redis_client
from app.services.chat_service import ChatService
from app.services.message_service import MessageService
from app.models.chat import ChatCreate, ChatResponse, ChatDeleteResponse, GroupChatCreate, ChatUpdate, PrivateChatRequest, ChatBackgroundUpdate, ChatBackgroundResponse
from app.models.message import ALLOWED_REACTIONS, CommentCreate, CommentUpdate, MessageCreate, MessageForwardRequest, MessageReactionSet, MessageResponse, MessageUpdate
from app.repositories.auth_repository import AuthRepository

from app.api.v1.notifications import send_fcm_notification

router = APIRouter()
logger = logging.getLogger(__name__)
CALL_TIMEOUT_SECONDS = 30
TERMINAL_CALL_SIGNALS = {"decline_call", "cancel_call", "missed_call", "end_call"}
active_call_timeouts: dict[str, asyncio.Task] = {}
# Kept only as a graceful fallback if Redis is temporarily unavailable.  The
# authoritative pending-call record is Redis so a cold-start request is not
# tied to the uvicorn process that received the offer.
pending_calls: dict[str, dict] = {}
PENDING_CALL_KEY_PREFIX = "queenchat:pending_call:"

BACKGROUND_TYPES = {"default", "gradient", "image"}
BACKGROUND_PRESETS = {
    "aurora", "lavender", "sunset", "midnight", "ocean",
    "hearts", "stars", "bubbles", "sparkles", "crown", "waves",
}


def _require_chat_participant(chat_id: str, current_user: User, db: Session):
    chat_id = validate_chat_id(chat_id)
    service = ChatService(db)
    chat = service.repo.get_chat(chat_id)
    if not chat or not service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="You are not a participant of this chat")
    return chat_id, chat


def _require_chat_background_editor(chat_id: str, current_user: User, db: Session):
    chat_id, chat = _require_chat_participant(chat_id, current_user, db)
    if chat.chat_type != "private" and chat.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only the chat creator can change this background")
    return chat_id, chat


class CallDeclineRequest(BaseModel):
    call_id: Optional[str] = Field(default=None, max_length=128)
    chat_id: str = Field(min_length=1, max_length=128)
    caller_id: str = Field(min_length=1, max_length=128)


async def _forward_webrtc_signal(
    *,
    target_user_id: str,
    sender_id: str,
    signal_data: dict,
    signal_type: str,
    chat_id: str,
    call_id: Optional[str],
    caller_name: Optional[str] = None,
    caller_username: Optional[str] = None,
    caller_avatar: Optional[str] = None,
) -> bool:
    payload = {
        "type": "webrtc_signal",
        "sender_id": str(sender_id),
        "signal": signal_data,
        "signal_type": signal_type,
        "chat_id": chat_id,
        "call_id": call_id,
        "caller_name": caller_name,
        "caller_username": caller_username,
        "caller_avatar": caller_avatar,
    }
    realtime_sent = await manager.send_global_message(str(target_user_id), payload)

    if chat_id in manager.active_connections:
        target_websockets = manager.active_connections[chat_id].get(str(target_user_id), set())
        for target_websocket in list(target_websockets):
            try:
                await target_websocket.send_json(payload)
                realtime_sent = True
            except Exception as e:
                logger.warning(
                    "WebRTC chat forwarding failed: target_user_id=%s chat_id=%s call_id=%s error_type=%s",
                    target_user_id,
                    chat_id,
                    call_id,
                    type(e).__name__,
                )
                manager.disconnect(chat_id, str(target_user_id), target_websocket)

    logger.info(
        "[CallTrace] webrtc_forwarded recipient_user_id=%s signal_type=%s call_id=%s realtime_sent=%s",
        target_user_id,
        signal_type,
        call_id,
        realtime_sent,
    )
    return realtime_sent


async def _handle_global_webrtc_signal(*, data: dict, user: User, db: Session) -> None:
    """Authorize and route call signalling independently of chat sockets."""
    target_user_id = data.get("target_user_id")
    signal_type = data.get("signal_type")
    signal_data = data.get("signal")
    call_id = data.get("call_id")
    chat_id = data.get("chat_id")

    if signal_type not in {"offer", "answer", "candidate", *TERMINAL_CALL_SIGNALS}:
        logger.warning("[CallFlow] rejected unknown signal sender_id=%s", user.id)
        return
    if not isinstance(target_user_id, str) or not target_user_id or target_user_id == str(user.id):
        logger.warning("[CallFlow] rejected invalid target sender_id=%s", user.id)
        return
    if not isinstance(chat_id, str) or not chat_id or not isinstance(signal_data, dict):
        logger.warning("[CallFlow] rejected malformed signal sender_id=%s signal_type=%s", user.id, signal_type)
        return
    if call_id is not None and (not isinstance(call_id, str) or len(call_id) > 128):
        logger.warning("[CallFlow] rejected invalid call_id sender_id=%s", user.id)
        return

    chat_service = ChatService(db)
    try:
        chat_id = validate_chat_id(chat_id)
    except HTTPException:
        return
    # The sender cannot choose an arbitrary recipient: both participants must
    # belong to the chat carried by this signalling envelope.
    if not chat_service.is_participant(chat_id, user.id) or not chat_service.is_participant(chat_id, target_user_id):
        logger.warning(
            "[CallFlow] rejected unauthorized signal sender_id=%s target_user_id=%s chat_id=%s",
            user.id, target_user_id, chat_id,
        )
        return

    caller_name = user.display_name or user.username
    logger.info(
        "[CallFlow] SIGNAL_RECEIVED call_id=%s sender_id=%s target_user_id=%s signal_type=%s",
        call_id, user.id, target_user_id, signal_type,
    )
    if signal_type == "offer":
        if call_id:
            _store_pending_call(call_id, {
                "caller_id": str(user.id), "callee_id": target_user_id,
                "chat_id": chat_id, "offer": signal_data, "candidates": [],
                "caller_name": caller_name, "caller_username": user.username,
                "caller_avatar": user.avatar, "created_at": int(time.time()),
            })
        _schedule_call_timeout(call_id=call_id, caller_id=str(user.id), callee_id=target_user_id, chat_id=chat_id)
    elif signal_type == "candidate":
        _append_pending_candidate(call_id, str(user.id), signal_data)
    elif signal_type in TERMINAL_CALL_SIGNALS:
        _cancel_call_timeout(call_id)
        _clear_pending_call(call_id)
    elif signal_type == "answer":
        _cancel_call_timeout(call_id)

    realtime_sent = await _forward_webrtc_signal(
        target_user_id=target_user_id, sender_id=str(user.id), signal_data=signal_data,
        signal_type=signal_type, chat_id=chat_id, call_id=call_id,
        caller_name=caller_name, caller_username=user.username, caller_avatar=user.avatar,
    )
    if signal_type == "answer" and realtime_sent:
        _clear_pending_call(call_id)
    if signal_type in TERMINAL_CALL_SIGNALS:
        await _forward_webrtc_signal(
            target_user_id=str(user.id), sender_id=target_user_id, signal_data=signal_data,
            signal_type=signal_type, chat_id=chat_id, call_id=call_id,
        )
    if signal_type == "offer":
        try:
            await send_fcm_notification(
                target_user_id, title="Incoming video call", body=f"{caller_name} is calling you",
                url=f"/chat/{chat_id}", event_type="incoming_call", chat_id=chat_id,
                sender_id=str(user.id), sender_name=caller_name, avatar=user.avatar,
                collapse_id=f"incoming_call:{call_id}" if call_id else f"incoming_call:{chat_id}:{user.id}:{int(time.time() * 1000)}",
                require_interaction=True, call_id=call_id, caller_id=str(user.id), call_type="video",
            )
        except Exception as push_error:
            logger.warning("Incoming call push failed: target_user_id=%s call_id=%s error_type=%s", target_user_id, call_id, type(push_error).__name__)


def _cancel_call_timeout(call_id: Optional[str]):
    if not call_id:
        return
    task = active_call_timeouts.pop(call_id, None)
    if task and not task.done():
        task.cancel()


def _pending_call_key(call_id: str) -> str:
    return f"{PENDING_CALL_KEY_PREFIX}{call_id}"


def _store_pending_call(call_id: Optional[str], pending: dict):
    if not call_id:
        return
    pending_calls[call_id] = pending
    try:
        redis_client.setex(_pending_call_key(call_id), CALL_TIMEOUT_SECONDS, json.dumps(pending))
    except Exception as error:
        logger.warning("Pending WebRTC call Redis write failed: call_id=%s error_type=%s", call_id, type(error).__name__)


def _get_pending_call(call_id: Optional[str]) -> Optional[dict]:
    if not call_id:
        return None
    try:
        raw = redis_client.get(_pending_call_key(call_id))
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
    except Exception as error:
        logger.warning("Pending WebRTC call Redis read failed: call_id=%s error_type=%s", call_id, type(error).__name__)
    return pending_calls.get(call_id)


def _append_pending_candidate(call_id: Optional[str], caller_id: str, candidate: dict):
    pending = _get_pending_call(call_id)
    if not pending or pending.get("caller_id") != caller_id:
        return
    pending.setdefault("candidates", []).append(candidate)
    _store_pending_call(call_id, pending)


def _clear_pending_call(call_id: Optional[str]):
    if not call_id:
        return
    pending_calls.pop(call_id, None)
    try:
        redis_client.delete(_pending_call_key(call_id))
    except Exception as error:
        logger.warning("Pending WebRTC call Redis delete failed: call_id=%s error_type=%s", call_id, type(error).__name__)


def _schedule_call_timeout(*, call_id: Optional[str], caller_id: str, callee_id: str, chat_id: str):
    if not call_id:
        return
    _cancel_call_timeout(call_id)

    async def timeout_call():
        try:
            await asyncio.sleep(CALL_TIMEOUT_SECONDS)
            await _forward_webrtc_signal(
                target_user_id=callee_id,
                sender_id=caller_id,
                signal_data={},
                signal_type="missed_call",
                chat_id=chat_id,
                call_id=call_id,
            )
            await _forward_webrtc_signal(
                target_user_id=caller_id,
                sender_id=callee_id,
                signal_data={},
                signal_type="missed_call",
                chat_id=chat_id,
                call_id=call_id,
            )
            logger.info(
                "WebRTC call timed out: call_id=%s caller_id=%s callee_id=%s chat_id=%s",
                call_id,
                caller_id,
                callee_id,
                chat_id,
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(
                "WebRTC call timeout failed: call_id=%s caller_id=%s callee_id=%s chat_id=%s error_type=%s",
                call_id,
                caller_id,
                callee_id,
                chat_id,
                type(e).__name__,
            )
        finally:
            active_call_timeouts.pop(call_id, None)
            _clear_pending_call(call_id)

    active_call_timeouts[call_id] = asyncio.create_task(timeout_call())


def validate_chat_id(chat_id: str):
    if not chat_id or chat_id == "undefined" or chat_id == "null":
        raise HTTPException(status_code=400, detail="Invalid chat ID")
    return chat_id


def _message_images(message) -> list[str] | None:
    if not getattr(message, "images", None):
        return None
    if isinstance(message.images, list):
        return message.images
    try:
        images = json.loads(message.images)
        return images if isinstance(images, list) else None
    except (TypeError, json.JSONDecodeError):
        return None


def _message_response(message, reactions: list[dict] | None = None) -> MessageResponse:
    forwarded_from_user = getattr(message, "forwarded_from_user", None)
    return MessageResponse(
        id=message.id,
        chat_id=message.chat_id,
        sender_id=message.sender_id,
        content=message.content,
        sticker_id=message.sticker_id,
        is_sticker=message.is_sticker,
        is_image=message.is_image,
        images=_message_images(message),
        media=json.loads(message.media) if getattr(message, 'media', None) else None,
        reply_to_id=message.reply_to_id,
        forwarded_from_message_id=getattr(message, "forwarded_from_message_id", None),
        forwarded_from_user_id=getattr(message, "forwarded_from_user_id", None),
        forwarded_from_user_name=(forwarded_from_user.display_name or forwarded_from_user.username) if forwarded_from_user else None,
        created_at=message.created_at,
        edited_at=getattr(message, "edited_at", None),
        deleted_at=getattr(message, "deleted_at", None),
        is_read=message.is_read,
        reactions=reactions or [],
        comments_count=0,
    )


def _message_preview(message) -> str:
    if getattr(message, "deleted_at", None):
        return "Message deleted"
    try:
        media = json.loads(message.media) if getattr(message, "media", None) else None
    except (TypeError, json.JSONDecodeError):
        media = None
    if media and media.get("type") == "voice":
        return "Голосовое сообщение"
    if media and media.get("type") == "video_note":
        return "Видеосообщение"
    content = (message.content or "").strip()
    legacy_image_content = content.startswith("/uploads/") or content.startswith('["/uploads/')
    if content and not (message.is_image and legacy_image_content):
        return content[:100] + ("..." if len(content) > 100 else "")
    if message.is_image:
        return "Image"
    if getattr(message, "is_sticker", False):
        return "Sticker"
    return ""


def _reaction_notification_preview(message) -> str:
    return _message_preview(message)


def _message_ws_payload(message, sender_username: str) -> dict:
    forwarded_from_user = getattr(message, "forwarded_from_user", None)
    return {
        "id": message.id,
        "sender_id": message.sender_id,
        "sender_name": sender_username,
        "content": message.content,
        "created_at": message.created_at,
        "chat_id": message.chat_id,
        "is_sticker": getattr(message, 'is_sticker', False),
        "is_read": message.is_read,
        "is_image": message.is_image,
        "images": _message_images(message),
        "media": json.loads(message.media) if getattr(message, 'media', None) else None,
        "reply_to_id": message.reply_to_id,
        "forwarded_from_message_id": getattr(message, "forwarded_from_message_id", None),
        "forwarded_from_user_id": getattr(message, "forwarded_from_user_id", None),
        "forwarded_from_user_name": (forwarded_from_user.display_name or forwarded_from_user.username) if forwarded_from_user else None,
        "edited_at": getattr(message, "edited_at", None),
        "deleted_at": getattr(message, "deleted_at", None),
        "reactions": []
    }


def _assert_reaction_target(
    chat_id: str,
    message_id: str,
    current_user: User,
    message_service: MessageService,
    chat_service: ChatService,
):
    chat = chat_service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if not chat_service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant")

    message = message_service.get_message(message_id)
    if not message or message.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Message not found")
    if getattr(message, "deleted_at", None):
        raise HTTPException(status_code=404, detail="Message deleted")

    return message


def _assert_editable_message(
    chat_id: str,
    message_id: str,
    current_user: User,
    message_service: MessageService,
    chat_service: ChatService,
):
    message = _assert_reaction_target(chat_id, message_id, current_user, message_service, chat_service)
    if message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only modify your own messages")
    return message


async def _send_reaction_update_to_participants(
    chat_id: str,
    message_id: str,
    message_service: MessageService,
    chat_service: ChatService,
):
    chat = chat_service.get_chat(chat_id)
    if not chat:
        return

    for participant in chat.participants:
        reactions = message_service.get_reaction_summaries([message_id], participant.user_id).get(message_id, [])
        await manager.send_personal_message(
            {
                "type": "message_reaction_updated",
                "chat_id": chat_id,
                "message_id": message_id,
                "reactions": reactions,
            },
            chat_id=chat_id,
            user_id=participant.user_id
        )


@router.post("/calls/decline")
async def decline_call_from_notification(
    payload: CallDeclineRequest,
    current_user: User = Depends(get_current_user),
):
    chat_id = validate_chat_id(payload.chat_id)
    _cancel_call_timeout(payload.call_id)
    _clear_pending_call(payload.call_id)
    await _forward_webrtc_signal(
        target_user_id=str(payload.caller_id),
        sender_id=str(current_user.id),
        signal_data={},
        signal_type="decline_call",
        chat_id=chat_id,
        call_id=payload.call_id,
    )
    await _forward_webrtc_signal(
        target_user_id=str(current_user.id),
        sender_id=str(payload.caller_id),
        signal_data={},
        signal_type="decline_call",
        chat_id=chat_id,
        call_id=payload.call_id,
    )
    return {"status": "ok"}


@router.websocket("/ws/global")
async def global_websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    db: Session = Depends(get_db)
):
    user = await get_current_user_ws(websocket, token, db)
    if not user:
        return
    if manager.connection_count(user.id) >= MAX_WS_PER_USER:
        logger.warning("WS_CONNECTION_LIMIT user_id=%s", user.id)
        await websocket.close(code=4008, reason="Too many connections")
        return
    await manager.connect_global(user.id, websocket)
    logger.info('[SocketTrace] global_ws_connected user_id=%s', user.id)
    try:
        while True:
            data = await websocket.receive_json()
            hit(WS_EVENTS, user.id)
            if data.get("type") == "ping":
                request_id = data.get("request_id")
                logger.debug("[WSHealth] ping received: scope=global user_id=%s request_id=%s", user.id, request_id)
                await websocket.send_json({"type": "pong", "request_id": request_id})
                logger.debug("[WSHealth] pong sent: scope=global user_id=%s request_id=%s", user.id, request_id)
            elif data.get("type") == "webrtc":
                await _handle_global_webrtc_signal(data=data, user=user, db=db)
    except WebSocketDisconnect as disconnect:
        logger.info(
            '[SocketTrace] global_ws_disconnected user_id=%s reason=client_or_proxy_close code=%s',
            user.id,
            disconnect.code,
        )
        manager.disconnect_global(user.id, websocket)
    except Exception as exc:
        logger.exception(
            '[SocketTrace] global_ws_exception user_id=%s type=%s',
            user.id,
            type(exc).__name__,
        )
        manager.disconnect_global(user.id, websocket)
        try:
            await websocket.close(code=1011, reason='Internal server error')
        except Exception:
            pass


@router.websocket("/ws/{chat_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    chat_id: str,
    token: str = Query(...),
    db: Session = Depends(get_db)
):
    try:
        chat_id = validate_chat_id(chat_id)
    except HTTPException:
        await websocket.close(code=4000, reason="Invalid chat ID")
        return
    
    user = await get_current_user_ws(websocket, token, db)
    if not user:
        return
    if manager.connection_count(user.id) >= MAX_WS_PER_USER:
        logger.warning("WS_CONNECTION_LIMIT user_id=%s", user.id)
        await websocket.close(code=4008, reason="Too many connections")
        return
    
    chat_service = ChatService(db)
    
    if not chat_service.is_participant(chat_id, user.id):
        try:
            chat_service.add_participant(chat_id, user.id)
            db.commit()
            print(f"✅ WebSocket: Auto-added user {user.id} to chat {chat_id}")
        except Exception as e:
            logger.warning(
                "WebSocket participant auto-add failed: user_id=%s chat_id=%s error_type=%s",
                user.id,
                chat_id,
                type(e).__name__,
            )
            await websocket.close(code=4005, reason="Not a participant")
            return
    
    await manager.connect(chat_id, user.id, websocket)
    logger.info('[SocketTrace] chat_ws_connected user_id=%s chat_id=%s', user.id, chat_id)
    
    try:
        while True:
            data = await websocket.receive_json()
            hit(WS_EVENTS, user.id)

            logger.debug(
                "WebSocket message received: chat_id=%s user_id=%s type=%s signal_type=%s",
                chat_id,
                user.id,
                data.get("type"),
                data.get("signal_type"),
            )
            
            if data.get("type") == "webrtc":
                # Call signalling is intentionally global.  A chat WebSocket
                # can be replaced whenever the active ChatRoom changes.
                logger.warning("[CallFlow] rejected chat-scoped signalling sender_id=%s chat_id=%s", user.id, chat_id)
                await websocket.send_json({"type": "error", "message": "WebRTC signalling must use the global socket"})
                continue
                target_user_id = data.get("target_user_id")
                signal_data = data.get("signal")
                signal_type = data.get("signal_type", "unknown")
                signal_chat_id = data.get("chat_id") or chat_id
                call_id = data.get("call_id")
                
                logger.info(
                    "[CallTrace] webrtc_received sender_user_id=%s recipient_user_id=%s chat_id=%s signal_type=%s call_id=%s",
                    user.id,
                    target_user_id,
                    signal_chat_id,
                    signal_type,
                    call_id,
                )
	                
                if target_user_id and signal_data is not None:
                    caller_name = user.display_name or user.username
                    if signal_type == "offer":
                        if call_id:
                            _store_pending_call(call_id, {
                                "caller_id": str(user.id),
                                "callee_id": str(target_user_id),
                                "chat_id": signal_chat_id,
                                "offer": signal_data,
                                "candidates": [],
                                "caller_name": caller_name,
                                "caller_username": user.username,
                                "caller_avatar": user.avatar,
                                "created_at": int(time.time()),
                            })
                        _schedule_call_timeout(
                            call_id=call_id,
                            caller_id=str(user.id),
                            callee_id=str(target_user_id),
                            chat_id=signal_chat_id,
                        )
                    elif signal_type == "candidate":
                        _append_pending_candidate(call_id, str(user.id), signal_data)
                    elif signal_type in TERMINAL_CALL_SIGNALS:
                        _cancel_call_timeout(call_id)
                        _clear_pending_call(call_id)
                    elif signal_type == "answer":
                        _cancel_call_timeout(call_id)

                    realtime_sent = await _forward_webrtc_signal(
                        target_user_id=str(target_user_id),
                        sender_id=str(user.id),
                        signal_data=signal_data,
                        signal_type=signal_type,
                        chat_id=signal_chat_id,
                        call_id=call_id,
                        caller_name=caller_name,
                        caller_username=user.username,
                        caller_avatar=user.avatar,
                    )

                    # Keep offer and early ICE candidates available until the
                    # answer has actually been forwarded to the caller.
                    if signal_type == "answer" and realtime_sent:
                        _clear_pending_call(call_id)

                    if signal_type in TERMINAL_CALL_SIGNALS:
                        await _forward_webrtc_signal(
                            target_user_id=str(user.id),
                            sender_id=str(target_user_id),
                            signal_data=signal_data,
                            signal_type=signal_type,
                            chat_id=signal_chat_id,
                            call_id=call_id,
                            caller_name=caller_name,
                            caller_username=user.username,
                            caller_avatar=user.avatar,
                        )

                    if signal_type == "offer":
                        try:
                            await send_fcm_notification(
                                str(target_user_id),
                                title="Incoming video call",
                                body=f"{caller_name} is calling you",
                                url=f"/chat/{signal_chat_id}",
                                event_type="incoming_call",
                                chat_id=signal_chat_id,
                                sender_id=str(user.id),
                                sender_name=caller_name,
                                avatar=user.avatar,
                                collapse_id=f"incoming_call:{call_id}" if call_id else f"incoming_call:{signal_chat_id}:{user.id}:{int(time.time() * 1000)}",
                                require_interaction=True,
                                call_id=call_id,
                                caller_id=str(user.id),
                                call_type="video",
                            )
                        except Exception as push_error:
                            logger.warning(
                                "Incoming call push failed: target_user_id=%s chat_id=%s call_id=%s error_type=%s",
                                target_user_id,
                                signal_chat_id,
                                call_id,
                                type(push_error).__name__,
                            )
                continue
            
            if data.get("type") == "ping":
                request_id = data.get("request_id")
                logger.debug("[WSHealth] ping received: chat_id=%s user_id=%s request_id=%s", chat_id, user.id, request_id)
                await websocket.send_json({"type": "pong", "request_id": request_id})
                logger.debug("[WSHealth] pong sent: chat_id=%s user_id=%s request_id=%s", chat_id, user.id, request_id)
                continue
            
            await manager.broadcast_to_chat(
                {"type": "new_message", "message": data.get("message", data)},
                chat_id=chat_id,
                exclude_user_id=user.id
            )
    except WebSocketDisconnect:
        manager.disconnect(chat_id, user.id, websocket)
        await manager.broadcast_to_chat(
            {"type": "user_left", "user_id": user.id},
            chat_id=chat_id
        )


@router.get("/{chat_id}/calls/{call_id}/pending")
def get_pending_call(
    chat_id: str,
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chat_id, _ = _require_chat_participant(chat_id, current_user, db)
    pending = _get_pending_call(call_id)
    if not pending or pending.get("chat_id") != chat_id:
        raise HTTPException(status_code=404, detail="Call is no longer available")
    if pending.get("callee_id") != str(current_user.id):
        raise HTTPException(status_code=403, detail="This incoming call belongs to another user")
    if int(time.time()) - int(pending.get("created_at", 0)) > CALL_TIMEOUT_SECONDS:
        _cancel_call_timeout(call_id)
        _clear_pending_call(call_id)
        raise HTTPException(status_code=410, detail="Call has expired")
    return {
        "call_id": call_id,
        "chat_id": chat_id,
        "caller_id": pending["caller_id"],
        "caller_name": pending.get("caller_name"),
        "caller_username": pending.get("caller_username"),
        "caller_avatar": pending.get("caller_avatar"),
        "call_type": "video",
        "offer": pending.get("offer"),
        "candidates": pending.get("candidates", []),
    }


@router.get("/{chat_id}/background", response_model=ChatBackgroundResponse)
def get_chat_background(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatBackgroundResponse:
    chat_id, _ = _require_chat_participant(chat_id, current_user, db)
    preference = db.query(ChatBackgroundPreferenceORM).filter(
        ChatBackgroundPreferenceORM.chat_id == chat_id,
    ).first()
    if not preference:
        return ChatBackgroundResponse()
    return ChatBackgroundResponse(
        background_type=preference.background_type,
        background_value=preference.background_value,
        updated_at=preference.updated_at,
        updated_by_user_id=preference.updated_by_user_id,
    )


@router.put("/{chat_id}/background", response_model=ChatBackgroundResponse)
async def save_chat_background(
    chat_id: str,
    payload: ChatBackgroundUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatBackgroundResponse:
    chat_id, _ = _require_chat_background_editor(chat_id, current_user, db)
    if payload.background_type not in BACKGROUND_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported background type")
    if payload.background_type == "gradient" and payload.background_value not in BACKGROUND_PRESETS:
        raise HTTPException(status_code=400, detail="Unsupported background preset")
    if payload.background_type == "image":
        prefix = f"/uploads/images/chat_background_{chat_id}_"
        if not payload.background_value or not payload.background_value.startswith(prefix):
            raise HTTPException(status_code=400, detail="Invalid background image")

    preference = db.query(ChatBackgroundPreferenceORM).filter(
        ChatBackgroundPreferenceORM.chat_id == chat_id,
    ).first()
    now = int(time.time())
    if not preference:
        preference = ChatBackgroundPreferenceORM(chat_id=chat_id)
        db.add(preference)
    preference.background_type = payload.background_type
    preference.background_value = None if payload.background_type == "default" else payload.background_value
    preference.updated_at = now
    preference.updated_by_user_id = current_user.id
    db.commit()
    background = ChatBackgroundResponse(
        background_type=preference.background_type,
        background_value=preference.background_value,
        updated_at=preference.updated_at,
        updated_by_user_id=preference.updated_by_user_id,
    )
    await manager.broadcast_to_chat({
        "type": "chat_background_updated",
        "chat_id": chat_id,
        "background": background.model_dump(),
    }, chat_id=chat_id)
    return background


@router.delete("/{chat_id}/background", status_code=204)
async def reset_chat_background(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chat_id, _ = _require_chat_background_editor(chat_id, current_user, db)
    db.query(ChatBackgroundPreferenceORM).filter(
        ChatBackgroundPreferenceORM.chat_id == chat_id,
    ).delete()
    db.commit()
    await manager.broadcast_to_chat({
        "type": "chat_background_updated",
        "chat_id": chat_id,
        "background": ChatBackgroundResponse().model_dump(),
    }, chat_id=chat_id)


@router.get("/{chat_id}", response_model=ChatResponse)
def get_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    chat_id = validate_chat_id(chat_id)
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if not service.is_participant(chat_id, current_user.id):
        try:
            service.add_participant(chat_id, current_user.id)
            db.commit()
        except Exception:
            db.rollback()
    
    return chat


@router.delete("/{chat_id}", response_model=ChatDeleteResponse)
def delete_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatDeleteResponse:
    chat_id = validate_chat_id(chat_id)
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if not service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant")
    service.delete_chat(chat_id)
    return ChatDeleteResponse(id=chat_id)


@router.post("/private", response_model=ChatResponse, status_code=201)
def create_private_chat(
    req: PrivateChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    service = ChatService(db)
    username = req.username
    
    if username == current_user.username:
        raise HTTPException(status_code=400, detail="Cannot create chat with yourself")
    
    auth_repo = AuthRepository(db)
    other_user = auth_repo.get_by_username(username)
    if not other_user:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    
    existing = service.get_existing_private_chat(current_user.id, other_user.id)
    if existing:
        return service.get_chat(existing.id)
    
    chat = service.create_chat(
        name=None,
        is_group=False,
        created_by=current_user.id,
        participant_ids=[current_user.id, other_user.id],
        chat_type="private"
    )
    
    db.commit()
    return chat


@router.post("/group", response_model=ChatResponse, status_code=201)
def create_group_chat(
    group_data: GroupChatCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    """Create group chat"""
    service = ChatService(db)
    
    if not group_data.name or len(group_data.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="Group name is required")
    
    if len(group_data.participant_ids) < 2:
        raise HTTPException(status_code=400, detail="Group chat must have at least 2 participants")
    
    auth_repo = AuthRepository(db)
    participant_ids = [current_user.id]
    
    for username in group_data.participant_ids:
        if username == current_user.username:
            continue
        user = auth_repo.get_by_username(username)
        if not user:
            raise HTTPException(status_code=404, detail=f"User '{username}' not found")
        participant_ids.append(user.id)
    
    participant_ids = list(set(participant_ids))
    
    if len(participant_ids) < 2:
        raise HTTPException(status_code=400, detail="Group chat must have at least 2 participants")
    
    chat = service.create_chat(
        name=group_data.name,
        is_group=True,
        created_by=current_user.id,
        participant_ids=participant_ids,
        chat_type="group"
    )
    
    db.commit()
    return chat


@router.post("/channel", response_model=ChatResponse, status_code=201)
def create_channel(
    channel_data: GroupChatCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    """Create channel (creator only, subscribers join themselves)"""
    service = ChatService(db)
    
    if not channel_data.name or len(channel_data.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="Channel name is required")
    
    # Channel starts with only creator
    participant_ids = [current_user.id]
    
    chat = service.create_chat(
        name=channel_data.name,
        is_group=False,
        created_by=current_user.id,
        participant_ids=participant_ids,
        chat_type="channel"
    )
    
    db.commit()
    return chat


@router.post("/", response_model=ChatResponse, status_code=201)
def create_chat(
    chat_data: ChatCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    if chat_data.chat_type == "group" or chat_data.is_group:
        return create_group_chat(
            GroupChatCreate(
                name=chat_data.name or "Group Chat",
                participant_ids=chat_data.participant_ids
            ),
            current_user,
            db
        )
    elif chat_data.chat_type == "channel":
        return create_channel(
            GroupChatCreate(
                name=chat_data.name or "Channel",
                participant_ids=[]
            ),
            current_user,
            db
        )
    else:
        if not chat_data.participant_ids or len(chat_data.participant_ids) == 0:
            raise HTTPException(status_code=400, detail="Username required")
        return create_private_chat(PrivateChatRequest(username=chat_data.participant_ids[0]), current_user, db)


@router.get("/", response_model=List[ChatResponse])
def get_user_chats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    type: str = None
) -> List[ChatResponse]:
    service = ChatService(db)
    
    if type == "channel":
        return service.get_all_channels()
    
    chats = service.get_user_chats(current_user.id)
    return chats


@router.post("/{chat_id}/messages", response_model=MessageResponse)
async def send_message(
    chat_id: str,
    message_data: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    chat_id = validate_chat_id(chat_id)
    
    print(f"🔵 [MESSAGE] Sending to chat {chat_id} from user {current_user.id}")
    
    try:
        message_service = MessageService(db)
        chat_service = ChatService(db)
        
        if not chat_service.is_participant(chat_id, current_user.id):
            print(f"⚠️ User {current_user.id} not in participants, adding...")
            chat_service.add_participant(chat_id, current_user.id)
            db.flush()
        
        chat = chat_service.get_chat(chat_id)
        
        # Channel permission: only creator can send messages
        if chat and chat.chat_type == "channel":
            if chat.created_by != current_user.id and current_user.username != "admin":
                raise HTTPException(status_code=403, detail="Only channel creator can post messages")
        
        images_json = json.dumps(message_data.images) if message_data.images else None
        media_json = json.dumps(message_data.media) if message_data.media else None
        if message_data.media:
            media = message_data.media
            expected = '/uploads/voice/' if media.get('type') == 'voice' else '/uploads/video_notes/'
            if media.get('type') not in {'voice', 'video_note'} or not str(media.get('url', '')).startswith(expected):
                raise HTTPException(status_code=400, detail='Invalid finalized media')

        message = message_service.create_message(
            chat_id=chat_id,
            sender_id=current_user.id,
            content=message_data.content or "",
            is_image=message_data.is_image or bool(message_data.images),
            images=images_json,
            media=media_json,
            reply_to_id=message_data.reply_to_id
        )
        
        db.commit()
        db.refresh(message)
        
        print(f"✅ Message {message.id} COMMITTED to DB")
        
        # Get recipients for FCM push notifications
        push_recipients = []
        chat = chat_service.get_chat(chat_id)
        
        if chat:
            for participant in chat.participants:
                if participant.user_id != current_user.id:
                    push_recipients.append(participant.user_id)
        
        print(f"🔔 [PUSH] push_recipients={push_recipients}")
        
        # Send FCM push notifications
        if push_recipients:
            push_body = _message_preview(message)

            sender_display_name = current_user.display_name or current_user.username
            chat_title = chat.name if chat and chat.name else sender_display_name
            event_type = "reply" if message.reply_to_id else "message"
            if message.content and any(f"@{participant.username}" in message.content for participant in chat.participants):
                event_type = "mention"
            
            for recipient_id in push_recipients:
                print(f"📨 [PUSH] Sending FCM to {recipient_id}")
                title = f"{sender_display_name} in {chat_title}" if chat and chat.chat_type != "private" else sender_display_name
                try:
                    await send_fcm_notification(
                        user_id=recipient_id,
                        title=title,
                        body=push_body,
                        url=f"/chat/{chat_id}?message={message.id}",
                        event_type=event_type,
                        chat_id=chat_id,
                        chat_type=chat.chat_type if chat else "private",
                        message_id=message.id,
                        sender_id=current_user.id,
                        sender_name=sender_display_name,
                        avatar=current_user.avatar,
                        chat_avatar=chat.avatar if chat else None,
                        collapse_id=f"chat:{chat_id}",
                    )
                except Exception as push_error:
                    logger.warning(
                        "Push notification failed after message save: recipient_user_id=%s chat_id=%s message_id=%s error_type=%s",
                        recipient_id,
                        chat_id,
                        message.id,
                        type(push_error).__name__,
                    )
        
        # Broadcast via WebSocket to online users
        await manager.broadcast_to_chat(
            {
                "type": "new_message",
                "message": _message_ws_payload(message, current_user.display_name or current_user.username)
            },
            chat_id=chat_id,
            exclude_user_id=current_user.id
        )
        
        return _message_response(message, [])
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(
            "Message send failed: chat_id=%s sender_user_id=%s error_type=%s",
            chat_id,
            current_user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{chat_id}/messages", response_model=List[MessageResponse])
def get_messages(
    chat_id: str,
    limit: int = None,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[MessageResponse]:
    chat_id = validate_chat_id(chat_id)
    message_service = MessageService(db)
    chat_service = ChatService(db)
    
    chat = chat_service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    if not chat_service.is_participant(chat_id, current_user.id):
        chat_service.add_participant(chat_id, current_user.id)
        db.commit()
    
    messages = message_service.get_chat_messages(chat_id, limit=limit, offset=offset)
    reaction_summaries = message_service.get_reaction_summaries(
        [message.id for message in messages],
        current_user.id
    )
    return [
        _message_response(message, reaction_summaries.get(message.id, []))
        for message in messages
    ]


@router.patch("/{chat_id}/messages/{message_id}", response_model=MessageResponse)
async def edit_message(
    chat_id: str,
    message_id: str,
    message_data: MessageUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    chat_id = validate_chat_id(chat_id)
    message_service = MessageService(db)
    chat_service = ChatService(db)
    message = _assert_editable_message(chat_id, message_id, current_user, message_service, chat_service)

    if getattr(message, "deleted_at", None):
        raise HTTPException(status_code=400, detail="Deleted messages cannot be edited")
    if getattr(message, "is_sticker", False):
        raise HTTPException(status_code=400, detail="Sticker messages cannot be edited")

    content = message_data.content.strip()
    has_images = bool(getattr(message, "is_image", False) or _message_images(message))
    if not content and not has_images:
        raise HTTPException(status_code=400, detail="Message content is required")

    try:
        updated_message = message_service.update_message_content(message_id, chat_id, content)
        db.commit()
        db.refresh(updated_message)
        reactions = message_service.get_reaction_summaries([message_id], current_user.id).get(message_id, [])
        event = {
            "type": "edit_message",
            "chat_id": chat_id,
            "message": _message_ws_payload(updated_message, current_user.display_name or current_user.username),
        }
        event["message"]["reactions"] = reactions
        await manager.broadcast_to_chat(event, chat_id=chat_id, exclude_user_id=None)
        return _message_response(updated_message, reactions)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(
            "Edit message failed: chat_id=%s message_id=%s user_id=%s error_type=%s",
            chat_id,
            message_id,
            current_user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{chat_id}/messages/{message_id}")
async def delete_message(
    chat_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chat_id = validate_chat_id(chat_id)
    message_service = MessageService(db)
    chat_service = ChatService(db)
    message = _assert_editable_message(chat_id, message_id, current_user, message_service, chat_service)

    if getattr(message, "deleted_at", None):
        return {"status": "ok", "message_id": message_id, "deleted_at": message.deleted_at}

    try:
        deleted_message = message_service.soft_delete_message(message_id, chat_id)
        message_service.remove_reaction_notifications_for_message(message_id)
        db.commit()
        db.refresh(deleted_message)
        event = {
            "type": "delete_message",
            "chat_id": chat_id,
            "message": _message_ws_payload(deleted_message, current_user.display_name or current_user.username),
        }
        event["message"]["reactions"] = []
        await manager.broadcast_to_chat(event, chat_id=chat_id, exclude_user_id=None)
        return {"status": "ok", "message_id": message_id, "deleted_at": deleted_message.deleted_at}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(
            "Delete message failed: chat_id=%s message_id=%s user_id=%s error_type=%s",
            chat_id,
            message_id,
            current_user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{chat_id}/messages/{message_id}/forward", response_model=MessageResponse)
async def forward_message(
    chat_id: str,
    message_id: str,
    forward_data: MessageForwardRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    source_chat_id = validate_chat_id(chat_id)
    target_chat_id = validate_chat_id(forward_data.target_chat_id)

    message_service = MessageService(db)
    chat_service = ChatService(db)

    source_chat = chat_service.get_chat(source_chat_id)
    if not source_chat:
        raise HTTPException(status_code=404, detail="Source chat not found")
    if not chat_service.is_participant(source_chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant in source chat")

    target_chat = chat_service.get_chat(target_chat_id)
    if not target_chat:
        raise HTTPException(status_code=404, detail="Target chat not found")
    if not chat_service.is_participant(target_chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant in target chat")
    if target_chat.chat_type == "channel" and target_chat.created_by != current_user.id and current_user.username != "admin":
        raise HTTPException(status_code=403, detail="Only channel creator can post messages")

    source_message = message_service.get_message(message_id)
    if not source_message or source_message.chat_id != source_chat_id:
        raise HTTPException(status_code=404, detail="Message not found")

    origin_message_id = source_message.forwarded_from_message_id or source_message.id
    origin_user_id = source_message.forwarded_from_user_id or source_message.sender_id

    try:
        forwarded_message = message_service.create_message(
            chat_id=target_chat_id,
            sender_id=current_user.id,
            content=source_message.content or "",
            sticker_id=source_message.sticker_id,
            is_image=source_message.is_image or bool(source_message.images),
            images=source_message.images,
            media=source_message.media,
            reply_to_id=None,
            forwarded_from_message_id=origin_message_id,
            forwarded_from_user_id=origin_user_id,
        )

        db.commit()
        db.refresh(forwarded_message)

        push_recipients = [
            participant.user_id
            for participant in target_chat.participants
            if participant.user_id != current_user.id
        ]

        if push_recipients:
            push_body = _message_preview(forwarded_message)
            sender_display_name = current_user.display_name or current_user.username
            chat_title = target_chat.name if target_chat and target_chat.name else sender_display_name
            event_type = "message"
            if forwarded_message.content and any(
                f"@{participant.username}" in forwarded_message.content
                for participant in target_chat.participants
            ):
                event_type = "mention"

            for recipient_id in push_recipients:
                try:
                    await send_fcm_notification(
                        user_id=recipient_id,
                        title=f"{sender_display_name} in {chat_title}" if target_chat.chat_type != "private" else sender_display_name,
                        body=push_body,
                        url=f"/chat/{target_chat_id}?message={forwarded_message.id}",
                        event_type=event_type,
                        chat_id=target_chat_id,
                        chat_type=target_chat.chat_type,
                        message_id=forwarded_message.id,
                        sender_id=current_user.id,
                        sender_name=sender_display_name,
                        avatar=current_user.avatar,
                        chat_avatar=target_chat.avatar,
                        collapse_id=f"chat:{target_chat_id}",
                    )
                except Exception as push_error:
                    logger.warning(
                        "Push notification failed after forward: recipient_user_id=%s chat_id=%s message_id=%s error_type=%s",
                        recipient_id,
                        target_chat_id,
                        forwarded_message.id,
                        type(push_error).__name__,
                    )

        await manager.broadcast_to_chat(
            {
                "type": "new_message",
                "message": _message_ws_payload(forwarded_message, current_user.display_name or current_user.username)
            },
            chat_id=target_chat_id,
            exclude_user_id=current_user.id
        )

        return _message_response(forwarded_message, [])

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(
            "Forward message failed: source_chat_id=%s target_chat_id=%s message_id=%s user_id=%s error_type=%s",
            source_chat_id,
            target_chat_id,
            message_id,
            current_user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail=str(e))


def _comment_target(channel_id: str, message_id: str, user: User, db: Session):
    chat = ChatService(db).get_chat(channel_id)
    if not chat or chat.chat_type != "channel":
        raise HTTPException(status_code=400, detail="Comments are available only in channels")
    if not ChatService(db).is_participant(channel_id, user.id):
        raise HTTPException(status_code=403, detail="Not a participant")
    message = MessageService(db).get_message(message_id)
    if not message or message.chat_id != channel_id or message.deleted_at:
        raise HTTPException(status_code=404, detail="Channel post not found")
    return message

def _comment_payload(comment):
    return {"id": comment.id, "message_id": comment.message_id, "channel_id": comment.channel_id,
            "user_id": comment.user_id, "username": comment.user.username,
            "display_name": comment.user.display_name, "avatar": comment.user.avatar,
            "content": comment.content, "created_at": comment.created_at, "edited_at": comment.edited_at, "deleted_at": comment.deleted_at}

def _comments_count(db: Session, message_id: str) -> int:
    return db.query(MessageCommentORM).filter(MessageCommentORM.message_id == message_id, MessageCommentORM.deleted_at.is_(None)).count()

@router.get("/{chat_id}/messages/{message_id}/comments")
def get_comments(chat_id: str, message_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _comment_target(validate_chat_id(chat_id), message_id, current_user, db)
    comments = db.query(MessageCommentORM).filter(MessageCommentORM.message_id == message_id).order_by(MessageCommentORM.created_at.asc()).all()
    return {"comments": [_comment_payload(comment) for comment in comments], "comments_count": _comments_count(db, message_id)}

@router.post("/{chat_id}/messages/{message_id}/comments")
async def create_comment(chat_id: str, message_id: str, payload: CommentCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    channel_id = validate_chat_id(chat_id); post = _comment_target(channel_id, message_id, current_user, db)
    comment = MessageCommentORM(id=str(__import__('uuid').uuid4()), message_id=message_id, channel_id=channel_id, user_id=current_user.id, content=payload.content.strip(), created_at=int(time.time()))
    db.add(comment); db.commit(); db.refresh(comment)
    event = {"type":"comment_created", "channel_id":channel_id, "message_id":message_id, "comment":_comment_payload(comment), "comments_count":_comments_count(db,message_id)}
    await manager.broadcast_to_chat(event, channel_id)
    if post.sender_id != current_user.id:
        commenter_name = current_user.display_name or current_user.username
        await send_fcm_notification(post.sender_id, "Новый комментарий", f'{commenter_name} прокомментировал вашу публикацию:\n"{comment.content[:100]}"', url=f"/chat/{channel_id}?message={message_id}", event_type="message_comment", chat_id=channel_id, message_id=message_id, sender_id=current_user.id, sender_name=commenter_name)
    return event

@router.patch("/{chat_id}/messages/{message_id}/comments/{comment_id}")
async def edit_comment(chat_id: str, message_id: str, comment_id: str, payload: CommentUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    channel_id=validate_chat_id(chat_id); _comment_target(channel_id,message_id,current_user,db)
    comment=db.query(MessageCommentORM).filter(MessageCommentORM.id==comment_id,MessageCommentORM.message_id==message_id,MessageCommentORM.channel_id==channel_id).first()
    if not comment: raise HTTPException(status_code=404,detail="Comment not found")
    if comment.user_id != current_user.id: raise HTTPException(status_code=403,detail="Only comment author can edit")
    comment.content=payload.content.strip(); comment.edited_at=int(time.time()); db.commit(); db.refresh(comment)
    event={"type":"comment_edited","channel_id":channel_id,"message_id":message_id,"comment":_comment_payload(comment),"comments_count":_comments_count(db,message_id)}; await manager.broadcast_to_chat(event,channel_id); return event

@router.delete("/{chat_id}/messages/{message_id}/comments/{comment_id}")
async def delete_comment(chat_id: str, message_id: str, comment_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    channel_id=validate_chat_id(chat_id); _comment_target(channel_id,message_id,current_user,db)
    comment=db.query(MessageCommentORM).filter(MessageCommentORM.id==comment_id,MessageCommentORM.message_id==message_id,MessageCommentORM.channel_id==channel_id).first()
    if not comment: raise HTTPException(status_code=404,detail="Comment not found")
    if comment.user_id != current_user.id: raise HTTPException(status_code=403,detail="Only comment author can delete")
    comment.deleted_at=int(time.time()); db.commit(); db.refresh(comment)
    event={"type":"comment_deleted","channel_id":channel_id,"message_id":message_id,"comment":_comment_payload(comment),"comments_count":_comments_count(db,message_id)}; await manager.broadcast_to_chat(event,channel_id); return event

@router.put("/{chat_id}/messages/{message_id}/reaction")
async def set_message_reaction(
    chat_id: str,
    message_id: str,
    reaction_data: MessageReactionSet,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    emoji = reaction_data.emoji
    if emoji not in ALLOWED_REACTIONS:
        raise HTTPException(status_code=400, detail="Unsupported reaction")

    message_service = MessageService(db)
    chat_service = ChatService(db)
    message = _assert_reaction_target(chat_id, message_id, current_user, message_service, chat_service)

    try:
        previous_reaction = message_service.get_reaction(message_id, current_user.id)
        if previous_reaction and previous_reaction.emoji == emoji:
            reactions = message_service.get_reaction_summaries([message_id], current_user.id).get(message_id, [])
            return {"type": "message_reaction_updated", "chat_id": chat_id, "message_id": message_id, "reactions": reactions}
        message_service.set_reaction(message_id, current_user.id, emoji)
        should_notify = message.sender_id != current_user.id
        if should_notify:
            message_service.upsert_reaction_notification(
                user_id=message.sender_id, chat_id=chat_id, message_id=message_id,
                reaction_user_id=current_user.id, emoji=emoji,
            )
        db.commit()
        reactions = message_service.get_reaction_summaries([message_id], current_user.id).get(message_id, [])
        event = {
            "type": "message_reaction_updated",
            "chat_id": chat_id,
            "message_id": message_id,
            "reactions": reactions,
        }
        await _send_reaction_update_to_participants(chat_id, message_id, message_service, chat_service)
        if should_notify:
            preview = _reaction_notification_preview(message)
            reactor_name = current_user.display_name or current_user.username
            body = f"{reactor_name} поставил {emoji} на ваше сообщение"
            if preview:
                body = f'{body}\n"{preview}"'
            notification_event = {
                "type": "message_reaction_notification", "chat_id": chat_id, "message_id": message_id,
                "emoji": emoji, "reactor_id": current_user.id, "reactor_name": reactor_name,
            }
            await manager.send_global_message(message.sender_id, notification_event)
            await manager.send_personal_message(notification_event, chat_id, message.sender_id)
            await send_fcm_notification(
                message.sender_id, "Новая реакция", body,
                url=f"/chat/{chat_id}?message={message_id}", event_type="message_reaction",
                chat_id=chat_id, message_id=message_id, sender_id=current_user.id,
                sender_name=reactor_name, reaction=emoji, reactor_id=current_user.id,
                reactor_name=reactor_name, collapse_id=f"reaction:{message_id}:{current_user.id}",
            )
        return event
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(
            "Set message reaction failed: chat_id=%s message_id=%s user_id=%s error_type=%s",
            chat_id,
            message_id,
            current_user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{chat_id}/messages/{message_id}/reaction")
async def delete_message_reaction(
    chat_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    message_service = MessageService(db)
    chat_service = ChatService(db)
    message = _assert_reaction_target(chat_id, message_id, current_user, message_service, chat_service)

    try:
        deleted = message_service.delete_reaction(message_id, current_user.id)
        if deleted and message.sender_id != current_user.id:
            message_service.remove_reaction_notification(
                user_id=message.sender_id, message_id=message_id, reaction_user_id=current_user.id,
            )
        db.commit()
        reactions = message_service.get_reaction_summaries([message_id], current_user.id).get(message_id, [])
        event = {
            "type": "message_reaction_updated",
            "chat_id": chat_id,
            "message_id": message_id,
            "reactions": reactions,
        }
        await _send_reaction_update_to_participants(chat_id, message_id, message_service, chat_service)
        if deleted and message.sender_id != current_user.id:
            notification_event = {
                "type": "message_reaction_notification_removed", "chat_id": chat_id,
                "message_id": message_id, "reactor_id": current_user.id,
            }
            await manager.send_global_message(message.sender_id, notification_event)
            await manager.send_personal_message(notification_event, chat_id, message.sender_id)
        return event
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(
            "Delete message reaction failed: chat_id=%s message_id=%s user_id=%s error_type=%s",
            chat_id,
            message_id,
            current_user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{chat_id}/messages/{message_id}/read")
def mark_message_as_read(
    chat_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    
    try:
        service = MessageService(db)
        message = service.mark_as_read(message_id, current_user.id)
        
        if message:
            db.commit()
            asyncio.create_task(
                manager.broadcast_to_chat(
                    {
                        "type": "message_read",
                        "message_id": message_id,
                        "user_id": current_user.id,
                        "chat_id": chat_id
                    },
                    chat_id=chat_id,
                    exclude_user_id=None
                )
            )
            return {"status": "ok", "message_id": message_id, "is_read": True}
        else:
            return {"status": "not_found"}
            
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(
            "Mark message as read failed: chat_id=%s message_id=%s user_id=%s error_type=%s",
            chat_id,
            message_id,
            current_user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{chat_id}/participants/{user_id}")
async def add_participant(
    chat_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add participant to group chat (creator only)"""
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    if chat.chat_type == "private":
        raise HTTPException(status_code=400, detail="Cannot add participants to private chat")
    
    if chat.chat_type == "channel":
        raise HTTPException(status_code=400, detail="Channel subscribers join via /subscribe endpoint")
    
    # Groups: only creator can add participants
    if chat.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only chat creator can add participants")
    
    user = service.auth_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if service.is_participant(chat_id, user_id):
        return {"message": "Participant already in chat"}
    
    service.add_participant(chat_id, user_id)
    db.commit()
    await send_fcm_notification(
        user_id=user_id,
        title=chat.name or "QueenChat",
        body=f"{current_user.display_name or current_user.username} added you to the chat",
        url=f"/chat/{chat_id}",
        event_type="chat_invite",
        chat_id=chat_id,
        chat_type=chat.chat_type,
        sender_id=current_user.id,
        sender_name=current_user.display_name or current_user.username,
        avatar=current_user.avatar,
        chat_avatar=chat.avatar,
        collapse_id=f"chat:{chat_id}:system",
    )
    return {"message": "Participant added successfully"}


@router.delete("/{chat_id}/participants/{user_id}")
async def remove_participant(
    chat_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Remove participant from group or unsubscribe from channel"""
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    if chat.chat_type == "private":
        raise HTTPException(status_code=400, detail="Cannot remove participants from private chat")
    
    if chat.chat_type == "channel":
        # Cannot remove channel creator
        if user_id == chat.created_by:
            raise HTTPException(status_code=400, detail="Cannot remove channel creator")
        # Only creator can remove subscribers
        if chat.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="Only channel creator can remove subscribers")
    else:
        # Groups: creator can remove anyone, users can leave themselves
        if chat.created_by != current_user.id and user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Only chat creator or the user themselves can leave")
    
    if not service.is_participant(chat_id, user_id):
        raise HTTPException(status_code=404, detail="User not in chat")
    
    service.remove_participant(chat_id, user_id)
    db.commit()
    if user_id != current_user.id:
        await send_fcm_notification(
            user_id=user_id,
            title=chat.name or "QueenChat",
            body=f"{current_user.display_name or current_user.username} removed you from the chat",
            url="/chat",
            event_type="chat_removed",
            chat_id=chat_id,
            chat_type=chat.chat_type,
            sender_id=current_user.id,
            sender_name=current_user.display_name or current_user.username,
            avatar=current_user.avatar,
            chat_avatar=chat.avatar,
            collapse_id=f"chat:{chat_id}:system",
        )
    return {"message": "Participant removed successfully"}


@router.post("/{chat_id}/subscribe")
async def subscribe_to_channel(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Subscribe to a channel"""
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    if chat.chat_type != "channel":
        raise HTTPException(status_code=400, detail="Not a channel")
    
    if service.is_participant(chat_id, current_user.id):
        return {"message": "Already subscribed"}
    
    service.add_participant(chat_id, current_user.id)
    db.commit()
    if chat.created_by != current_user.id:
        await send_fcm_notification(
            user_id=chat.created_by,
            title=chat.name or "QueenChat",
            body=f"{current_user.display_name or current_user.username} subscribed to your channel",
            url=f"/chat/{chat_id}",
            event_type="channel_subscribe",
            chat_id=chat_id,
            chat_type=chat.chat_type,
            sender_id=current_user.id,
            sender_name=current_user.display_name or current_user.username,
            avatar=current_user.avatar,
            chat_avatar=chat.avatar,
            collapse_id=f"chat:{chat_id}:system",
        )
    return {"message": "Subscribed successfully"}


@router.post("/{chat_id}/unsubscribe")
async def unsubscribe_from_channel(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Unsubscribe from a channel"""
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    if chat.chat_type != "channel":
        raise HTTPException(status_code=400, detail="Not a channel")
    
    if not service.is_participant(chat_id, current_user.id):
        return {"message": "Not subscribed"}
    
    # Creator cannot unsubscribe from own channel
    if chat.created_by == current_user.id:
        raise HTTPException(status_code=400, detail="Channel creator cannot unsubscribe")
    
    service.remove_participant(chat_id, current_user.id)
    db.commit()
    if chat.created_by != current_user.id:
        await send_fcm_notification(
            user_id=chat.created_by,
            title=chat.name or "QueenChat",
            body=f"{current_user.display_name or current_user.username} unsubscribed from your channel",
            url=f"/chat/{chat_id}",
            event_type="channel_unsubscribe",
            chat_id=chat_id,
            chat_type=chat.chat_type,
            sender_id=current_user.id,
            sender_name=current_user.display_name or current_user.username,
            avatar=current_user.avatar,
            chat_avatar=chat.avatar,
            collapse_id=f"chat:{chat_id}:system",
        )
    return {"message": "Unsubscribed successfully"}


@router.get("/{chat_id}/last-message")
def get_last_message(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    
    try:
        message_service = MessageService(db)
        chat_service = ChatService(db)
        
        if not chat_service.is_participant(chat_id, current_user.id):
            raise HTTPException(status_code=403, detail="Not a participant")
        
        last_msg = message_service.get_last_message(chat_id)
        
        if last_msg:
            return {
                "id": last_msg.id,
                "content": _message_preview(last_msg),
                "created_at": last_msg.created_at,
                "sender_id": last_msg.sender_id,
                "sender_name": (last_msg.sender.display_name or last_msg.sender.username) if last_msg.sender else None,
                "is_image": getattr(last_msg, 'is_image', False),
                "images": _message_images(last_msg),
                "is_sticker": getattr(last_msg, 'is_sticker', False),
                "media": json.loads(last_msg.media) if getattr(last_msg, 'media', None) else None,
                "edited_at": getattr(last_msg, "edited_at", None),
                "deleted_at": getattr(last_msg, "deleted_at", None),
            }
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Get last message failed: chat_id=%s user_id=%s error_type=%s",
            chat_id,
            current_user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{chat_id}/messages/unread/count")
def get_unread_messages_count(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    
    message_service = MessageService(db)
    chat_service = ChatService(db)
    
    if not chat_service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant")
    
    count = message_service.get_unread_count(chat_id, current_user.id)
    
    return {"count": count}


@router.post("/{chat_id}/messages/read/all")
async def mark_all_messages_as_read(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        chat_id = validate_chat_id(chat_id)
    except HTTPException:
        raise HTTPException(status_code=400, detail="Invalid chat ID")
    
    message_service = MessageService(db)
    chat_service = ChatService(db)
    
    chat = chat_service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    if not chat_service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant")
    
    count = message_service.mark_all_as_read(chat_id, current_user.id)
    db.commit()
    
    return {"status": "ok", "marked_count": count}


@router.patch("/{chat_id}/reactions/read")
async def mark_reaction_notifications_read(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chat_id = validate_chat_id(chat_id)
    chat_service = ChatService(db)
    if not chat_service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant")
    count = MessageService(db).mark_reaction_notifications_read(chat_id, current_user.id)
    db.commit()
    await manager.send_global_message(current_user.id, {
        "type": "reaction_notifications_read", "chat_id": chat_id, "user_id": current_user.id,
    })
    return {"status": "ok", "marked_count": count}


@router.patch("/{chat_id}", response_model=ChatResponse)
async def update_chat(
    chat_id: str,
    chat_update: ChatUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    """Update chat name or avatar (creator only)"""
    chat_id = validate_chat_id(chat_id)
    service = ChatService(db)
    
    chat = service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    if chat.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only chat creator can update chat info")
    
    if chat.chat_type not in ["group", "channel"]:
        raise HTTPException(status_code=400, detail="Only groups and channels can be updated")
    
    updated_chat = service.update_chat(
        chat_id=chat_id,
        name=chat_update.name,
        avatar=chat_update.avatar
    )
    
    if not updated_chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    db.commit()
    changed = []
    if chat_update.name and chat_update.name != chat.name:
        changed.append("name")
    if chat_update.avatar is not None and chat_update.avatar != chat.avatar:
        changed.append("avatar")
    event_type = "chat_avatar_updated" if changed == ["avatar"] else "chat_updated"
    body = "Chat avatar updated" if changed == ["avatar"] else "Chat info updated"
    for participant in updated_chat.participants:
        if participant.user_id == current_user.id:
            continue
        await send_fcm_notification(
            user_id=participant.user_id,
            title=updated_chat.name or "QueenChat",
            body=body,
            url=f"/chat/{chat_id}",
            event_type=event_type,
            chat_id=chat_id,
            chat_type=updated_chat.chat_type,
            sender_id=current_user.id,
            sender_name=current_user.display_name or current_user.username,
            avatar=current_user.avatar,
            chat_avatar=updated_chat.avatar,
            collapse_id=f"chat:{chat_id}:system",
        )
    
    return updated_chat
    media = json.loads(message.media) if getattr(message, 'media', None) else None
    if media and media.get('type') == 'voice': return '🎤 Голосовое сообщение'
    if media and media.get('type') == 'video_note': return '◉ Видеосообщение'
