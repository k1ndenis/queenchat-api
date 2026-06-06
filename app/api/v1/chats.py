from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.core.websocket import manager, get_current_user_ws
from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.core.database import MessageORM
from app.services.chat_service import ChatService
from app.services.message_service import MessageService
from app.services.notification_service import NotificationService
from app.models.chat import ChatCreate, ChatResponse, ChatDeleteResponse
from app.models.message import MessageCreate, MessageResponse
from app.repositories.auth_repository import AuthRepository

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

@router.post("/", response_model=ChatResponse, status_code=201)
def create_chat(
    chat_data: ChatCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    service = ChatService(db)
    try:
        if chat_data.is_group:
            all_participants = list(set(chat_data.participant_ids + [current_user.id]))
            chat = service.create_chat(
                name=chat_data.name,
                is_group=True,
                created_by=current_user.id,
                participant_ids=all_participants
            )
        else:
            if not chat_data.participant_ids or len(chat_data.participant_ids) == 0:
                raise HTTPException(status_code=400, detail="Username required")
            
            other_username = chat_data.participant_ids[0]
            if not other_username:
                raise HTTPException(status_code=400, detail="Username required")
            
            auth_repo = AuthRepository(db)
            other_user = auth_repo.get_by_username(other_username)
            if not other_user:
                raise HTTPException(status_code=404, detail=f"User '{other_username}' not found")
            
            existing = service.repo.get_existing_private_chat(current_user.id, other_user.id)
            if existing:
                return service.get_chat(existing.id)
            
            chat = service.create_chat(
                name=None,
                is_group=False,
                created_by=current_user.id,
                participant_ids=[current_user.id, other_user.id]
            )
        
        db.commit()
        return chat
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"❌ Error creating chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
        
        message = message_service.create_message(
            chat_id=chat_id,
            sender_id=current_user.id,
            content=message_data.content
        )
        
        db.commit()
        db.refresh(message)
        
        print(f"✅ Message {message.id} COMMITTED to DB")
        
        print("=" * 60)
        print("🔔 [NOTIFICATION] Starting notification creation")
        print(f"🔔 [NOTIFICATION] Chat ID: {chat_id}")
        print(f"🔔 [NOTIFICATION] Current user: {current_user.id}")
        
        try:
            notification_service = NotificationService(db)
            chat = chat_service.get_chat(chat_id)
            
            if chat:
                print(f"🔔 [NOTIFICATION] Participants count: {len(chat.participants)}")
                for participant in chat.participants:
                    if participant.user_id != current_user.id:
                        print(f"🔔 [NOTIFICATION] Creating notification for {participant.user_id}")
                        notification_service.create_notification(
                            user_id=participant.user_id,
                            chat_id=chat_id,
                            title="Новое сообщение",
                            message=f"{current_user.username}: {message.content[:50]}",
                            type="message"
                        )
                        print(f"✅ [NOTIFICATION] Created for {participant.user_id}")
                    else:
                        print(f"🔔 [NOTIFICATION] Skipping sender {participant.user_id}")
            else:
                print(f"❌ [NOTIFICATION] Chat not found!")
                
        except Exception as notif_error:
            print(f"❌ [NOTIFICATION] Exception: {notif_error}")
            import traceback
            traceback.print_exc()
        
        print("=" * 60)
        
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
                    "is_read": message.is_read
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
            import asyncio
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
    chat_id = validate_chat_id(chat_id)
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    if not chat.is_group:
        if service.is_participant(chat_id, user_id):
            return {"message": "Participant already in chat"}
        service.add_participant(chat_id, user_id)
        db.commit()
        return {"message": "Participant added successfully"}
    
    if chat.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only chat creator can add participants")
    
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
    chat_id = validate_chat_id(chat_id)
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    if not chat or chat.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only chat creator can remove participants")
    service.remove_participant(chat_id, user_id)
    db.commit()
    return {"message": "Participant removed successfully"}

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
                "sender_name": last_msg.sender.username if last_msg.sender else None
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