from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List
import json
import asyncio
import time

from app.core.websocket import manager, get_current_user_ws
from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.services.chat_service import ChatService
from app.services.message_service import MessageService
from app.models.chat import ChatCreate, ChatResponse, ChatDeleteResponse, GroupChatCreate, ChatUpdate
from app.models.message import MessageCreate, MessageResponse
from app.repositories.auth_repository import AuthRepository

from app.api.v1.notifications import send_fcm_notification

router = APIRouter()


def validate_chat_id(chat_id: str):
    if not chat_id or chat_id == "undefined" or chat_id == "null":
        raise HTTPException(status_code=400, detail="Invalid chat ID")
    return chat_id


@router.websocket("/ws/global")
async def global_websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    db: Session = Depends(get_db)
):
    user = await get_current_user_ws(websocket, token, db)
    if not user:
        return
    await manager.connect_global(user.id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_global(user.id)


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
    
    chat_service = ChatService(db)
    
    if not chat_service.is_participant(chat_id, user.id):
        try:
            chat_service.add_participant(chat_id, user.id)
            db.commit()
            print(f"✅ WebSocket: Auto-added user {user.id} to chat {chat_id}")
        except Exception as e:
            print(f"❌ WebSocket: Failed to add participant: {e}")
            await websocket.close(code=4005, reason="Not a participant")
            return
    
    await manager.connect(chat_id, user.id, websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            await manager.broadcast_to_chat(
                {"type": "new_message", "message": data.get("message", data)},
                chat_id=chat_id,
                exclude_user_id=user.id
            )
    except WebSocketDisconnect:
        manager.disconnect(chat_id, user.id)
        await manager.broadcast_to_chat(
            {"type": "user_left", "user_id": user.id},
            chat_id=chat_id
        )


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
    username: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    """Create private 1-on-1 chat"""
    service = ChatService(db)
    
    if username == current_user.username:
        raise HTTPException(status_code=400, detail="Cannot create chat with yourself")
    
    auth_repo = AuthRepository(db)
    other_user = auth_repo.get_by_username(username)
    if not other_user:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    
    existing = service.repo.get_existing_private_chat(current_user.id, other_user.id)
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
        return create_private_chat(chat_data.participant_ids[0], current_user, db)


@router.get("/", response_model=List[ChatResponse])
def get_user_chats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[ChatResponse]:
    service = ChatService(db)
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
        
        message = message_service.create_message(
            chat_id=chat_id,
            sender_id=current_user.id,
            content=message_data.content,
            is_image=message_data.is_image or bool(message_data.images),
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
            push_body = message.content[:100] + ("..." if len(message.content) > 100 else "")
            if message.is_image:
                push_body = "🖼️ Image"
            if getattr(message, 'is_sticker', False):
                push_body = "🎨 Sticker"
            
            for recipient_id in push_recipients:
                print(f"📨 [PUSH] Sending FCM to {recipient_id}")
                await send_fcm_notification(
                    user_id=recipient_id,
                    title=f"New message from {current_user.username}",
                    body=push_body,
                    url=f"/chat/{chat_id}"
                )
        
        # Broadcast via WebSocket to online users
        await manager.broadcast_to_chat(
            {
                "type": "new_message",
                "message": {
                    "id": message.id,
                    "sender_id": message.sender_id,
                    "sender_name": current_user.username,
                    "content": message.content,
                    "created_at": message.created_at,
                    "chat_id": chat_id,
                    "is_sticker": getattr(message, 'is_sticker', False),
                    "is_read": message.is_read,
                    "is_image": message.is_image,
                    "images": message_data.images if message_data.images else None
                }
            },
            chat_id=chat_id,
            exclude_user_id=current_user.id
        )
        
        return message
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
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
    return messages


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
            
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{chat_id}/participants/{user_id}")
def add_participant(
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
    return {"message": "Participant added successfully"}


@router.delete("/{chat_id}/participants/{user_id}")
def remove_participant(
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
    return {"message": "Participant removed successfully"}


@router.post("/{chat_id}/subscribe")
def subscribe_to_channel(
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
    return {"message": "Subscribed successfully"}


@router.post("/{chat_id}/unsubscribe")
def unsubscribe_from_channel(
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
                "content": last_msg.content,
                "created_at": last_msg.created_at,
                "sender_id": last_msg.sender_id,
                "sender_name": last_msg.sender.username if last_msg.sender else None,
                "is_image": getattr(last_msg, 'is_image', False)
            }
        return None
        
    except Exception as e:
        print(f"❌ Error getting last message: {e}")
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


@router.patch("/{chat_id}", response_model=ChatResponse)
def update_chat(
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
    
    return updated_chat