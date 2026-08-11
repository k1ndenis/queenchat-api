"""Private pair-space and invite API.  A space always reuses a private chat."""
import hashlib
import secrets
import time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import (ChatORM, ChatParticipantORM, MessageORM, PrivateSpaceInviteORM,
    PrivateSpaceSettingsORM, SpaceDateORM, SpaceMemoryORM, SpaceNoteORM, UserORM)
from app.core.dependency import get_current_user, get_db
from app.core.rate_limit import INVITE_CREATE, hit
from app.services.chat_service import ChatService

router = APIRouter()
INVITE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_ACTIVE_INVITES = 5
THEMES = {"queen", "sunset", "midnight", "rose", "aurora"}

class InviteCreate(BaseModel):
    chat_id: Optional[str] = None

class SpaceActivate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    theme: str = "queen"

class SpaceSettingsUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    theme: Optional[str] = None

class SpaceDateInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    event_date: str = Field(min_length=10, max_length=10)
    emoji: str = Field(default="❤️", max_length=16)
    repeats_yearly: bool = True

class SpaceNoteInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(default="", max_length=10000)
    note_type: str = "note"
    due_date: Optional[str] = None
    completed: bool = False

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def _display(user: UserORM) -> str:
    return (user.display_name or user.username).strip()

def _require_private_participant(db: Session, chat_id: str, user_id: str) -> ChatORM:
    chat = db.get(ChatORM, chat_id)
    if not chat or chat.chat_type != "private" or chat.is_group:
        raise HTTPException(404, "Private space not found")
    if not db.query(ChatParticipantORM).filter_by(chat_id=chat_id, user_id=user_id).first():
        raise HTTPException(403, "You are not a participant of this private chat")
    if db.query(func.count(ChatParticipantORM.id)).filter_by(chat_id=chat_id).scalar() != 2:
        raise HTTPException(400, "A private space requires exactly two participants")
    return chat

def _space(db: Session, chat_id: str) -> PrivateSpaceSettingsORM:
    space = db.get(PrivateSpaceSettingsORM, chat_id)
    if not space or not space.enabled or space.status != "active":
        raise HTTPException(404, "Space is not enabled")
    return space

def _enable(db: Session, chat_id: str, title: Optional[str] = None, theme: str = "queen", status: str = "active") -> PrivateSpaceSettingsORM:
    now = int(time.time())
    space = db.get(PrivateSpaceSettingsORM, chat_id)
    if not space:
        space = PrivateSpaceSettingsORM(chat_id=chat_id, enabled=True, status=status, title=title, theme=theme, created_at=now, updated_at=now)
        db.add(space)
    else:
        space.enabled = True
        space.status = status
        if title is not None: space.title = title
        if theme in THEMES: space.theme = theme
        space.updated_at = now
    db.flush()
    return space

def _invite_state(invite: PrivateSpaceInviteORM, now: int) -> str:
    if invite.revoked_at: return "revoked"
    if invite.accepted_at: return "used"
    if invite.expires_at <= now: return "expired"
    return "active"

def _validate_iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, "Date must be YYYY-MM-DD")

def _note_dict(note: SpaceNoteORM) -> dict:
    return {"id": note.id, "title": note.title, "content": note.content, "type": note.note_type,
            "due_date": note.due_date, "completed": note.completed, "created_by": note.created_by,
            "created_at": note.created_at, "updated_at": note.updated_at}

def _date_dict(item: SpaceDateORM) -> dict:
    return {"id": item.id, "title": item.title, "event_date": item.event_date, "emoji": item.emoji,
            "repeats_yearly": item.repeats_yearly, "created_by": item.created_by, "created_at": item.created_at}

@router.get("/invites/{token}/preview")
def preview_invite(token: str, db: Session = Depends(get_db)):
    invite = db.query(PrivateSpaceInviteORM).filter_by(token_hash=_hash(token)).first()
    if not invite: return {"status": "invalid"}
    state = _invite_state(invite, int(time.time()))
    if state != "active": return {"status": state}
    creator = db.get(UserORM, invite.creator_user_id)
    recipient = db.get(UserORM, invite.recipient_user_id) if invite.recipient_user_id else None
    return {"status": "active", "creator": {"display_name": _display(creator), "avatar": creator.avatar}, "recipient": None if not recipient else {"display_name": _display(recipient), "avatar": recipient.avatar}, "expires_at": invite.expires_at}

@router.post("/invites", status_code=201)
def create_invite(body: InviteCreate, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    hit(INVITE_CREATE, current_user.id)
    now = int(time.time())
    active = db.query(func.count(PrivateSpaceInviteORM.id)).filter(PrivateSpaceInviteORM.creator_user_id == current_user.id, PrivateSpaceInviteORM.accepted_at.is_(None), PrivateSpaceInviteORM.revoked_at.is_(None), PrivateSpaceInviteORM.expires_at > now).scalar()
    if active >= MAX_ACTIVE_INVITES: raise HTTPException(429, "Maximum number of active invitations reached")
    if body.chat_id:
        chat = _require_private_participant(db, body.chat_id, current_user.id)
        recipient = next((p for p in chat.participants if p.id != current_user.id), None)
        if not recipient: raise HTTPException(400, "A private space requires another participant")
        existing = db.get(PrivateSpaceSettingsORM, body.chat_id)
        if existing and existing.status == "active": raise HTTPException(409, "Space is already active")
        _enable(db, body.chat_id, status="pending")
    token = secrets.token_urlsafe(32)
    invite = PrivateSpaceInviteORM(token_hash=_hash(token), creator_user_id=current_user.id, recipient_user_id=recipient.id if body.chat_id else None, chat_id=body.chat_id, expires_at=now + INVITE_TTL_SECONDS)
    db.add(invite); db.commit(); db.refresh(invite)
    return {"id": invite.id, "invite_url": f"https://queenchat.ru/invite/{token}", "expires_at": invite.expires_at, "chat_id": body.chat_id}

@router.get("/{chat_id}/state")
def space_state(chat_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id)
    space = db.get(PrivateSpaceSettingsORM, chat_id)
    if not space: return {"status": "not_created"}
    result = {"status": space.status, "created_at": space.created_at}
    if space.status == "pending":
        invite = db.query(PrivateSpaceInviteORM).filter(PrivateSpaceInviteORM.chat_id == chat_id, PrivateSpaceInviteORM.accepted_at.is_(None), PrivateSpaceInviteORM.revoked_at.is_(None), PrivateSpaceInviteORM.expires_at > int(time.time())).order_by(PrivateSpaceInviteORM.created_at.desc()).first()
        if invite:
            result.update({"invite_id": invite.id, "can_accept": invite.recipient_user_id == current_user.id, "created_by_me": invite.creator_user_id == current_user.id})
    return result

@router.post("/{chat_id}/accept-pending")
def accept_pending_space(chat_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id)
    invite = db.query(PrivateSpaceInviteORM).filter(PrivateSpaceInviteORM.chat_id == chat_id, PrivateSpaceInviteORM.recipient_user_id == current_user.id, PrivateSpaceInviteORM.accepted_at.is_(None), PrivateSpaceInviteORM.revoked_at.is_(None), PrivateSpaceInviteORM.expires_at > int(time.time())).with_for_update().first()
    if not invite: raise HTTPException(404, "Pending invitation not found")
    space = _enable(db, chat_id, status="active")
    invite.accepted_at, invite.accepted_by_user_id = int(time.time()), current_user.id
    db.commit()
    return {"chat_id": chat_id, "space": {"status": space.status, "theme": space.theme, "title": space.title}}

@router.get("/invites/active")
def active_invites(current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    now = int(time.time())
    rows = db.query(PrivateSpaceInviteORM).filter(PrivateSpaceInviteORM.creator_user_id == current_user.id, PrivateSpaceInviteORM.accepted_at.is_(None), PrivateSpaceInviteORM.revoked_at.is_(None), PrivateSpaceInviteORM.expires_at > now).order_by(PrivateSpaceInviteORM.created_at.desc()).all()
    return [{"id": x.id, "chat_id": x.chat_id, "expires_at": x.expires_at} for x in rows]

@router.delete("/invites/{invite_id}", status_code=204)
def revoke_invite(invite_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    invite = db.get(PrivateSpaceInviteORM, invite_id)
    if not invite or invite.creator_user_id != current_user.id: raise HTTPException(404, "Invitation not found")
    if not invite.accepted_at: invite.revoked_at = int(time.time()); db.commit()

@router.post("/invites/{token}/accept")
def accept_invite(token: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    invite = db.query(PrivateSpaceInviteORM).filter_by(token_hash=_hash(token)).with_for_update().first()
    if not invite: raise HTTPException(404, "Invitation not found")
    state = _invite_state(invite, int(time.time()))
    if state != "active": raise HTTPException(409, f"Invitation is {state}")
    if invite.creator_user_id == current_user.id: raise HTTPException(400, "You cannot accept your own invitation")
    # This serializes accepts for the same creator too, so two different links
    # cannot race past the existing-private-chat lookup on PostgreSQL.
    db.query(UserORM).filter(UserORM.id == invite.creator_user_id).with_for_update().first()
    service = ChatService(db)
    chat = service.get_existing_private_chat(invite.creator_user_id, current_user.id)
    if not chat:
        chat_response = service.create_chat(None, False, invite.creator_user_id, [invite.creator_user_id, current_user.id], "private")
        chat = service.repo.get_chat(chat_response.id)
    if invite.recipient_user_id and invite.recipient_user_id != current_user.id:
        raise HTTPException(403, "This invitation belongs to another user")
    if invite.chat_id:
        chat = _require_private_participant(db, invite.chat_id, current_user.id)
    space = _enable(db, chat.id, status="active")
    invite.chat_id, invite.accepted_at, invite.accepted_by_user_id = chat.id, int(time.time()), current_user.id
    db.commit()
    return {"chat_id": chat.id, "space": {"chat_id": space.chat_id, "theme": space.theme, "title": space.title}}

@router.post("/{chat_id}/activate")
def activate_space(chat_id: str, body: SpaceActivate, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.theme not in THEMES: raise HTTPException(422, "Unknown theme")
    _require_private_participant(db, chat_id, current_user.id)
    space = _enable(db, chat_id, body.title, body.theme, status="active"); db.commit()
    return {"chat_id": space.chat_id, "enabled": space.enabled, "title": space.title, "theme": space.theme, "created_at": space.created_at}

@router.put("/{chat_id}/settings")
def update_space_settings(chat_id: str, body: SpaceSettingsUpdate, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); space = _space(db, chat_id)
    if body.theme is not None and body.theme not in THEMES: raise HTTPException(422, "Unknown theme")
    if body.title is not None: space.title = body.title.strip() or None
    if body.theme is not None: space.theme = body.theme
    space.updated_at = int(time.time()); db.commit()
    return {"chat_id": space.chat_id, "title": space.title, "theme": space.theme}

@router.get("/{chat_id}")
def get_space(chat_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    chat = _require_private_participant(db, chat_id, current_user.id); space = _space(db, chat_id)
    people = [{"id": p.id, "display_name": _display(p), "avatar": p.avatar} for p in chat.participants]
    messages = db.query(func.count(MessageORM.id)).filter(MessageORM.chat_id == chat_id, MessageORM.deleted_at.is_(None)).scalar() or 0
    photos = db.query(func.count(MessageORM.id)).filter(MessageORM.chat_id == chat_id, MessageORM.is_image.is_(True), MessageORM.deleted_at.is_(None)).scalar() or 0
    title = space.title or " × ".join(x["display_name"] for x in people)
    memories = db.query(func.count(SpaceMemoryORM.id)).filter(SpaceMemoryORM.chat_id == chat_id).scalar() or 0
    nearest = db.query(SpaceDateORM).filter(SpaceDateORM.chat_id == chat_id).order_by(SpaceDateORM.event_date.asc()).first()
    plans = db.query(SpaceNoteORM).filter(SpaceNoteORM.chat_id == chat_id, SpaceNoteORM.note_type == "plan", SpaceNoteORM.completed.is_(False)).order_by(SpaceNoteORM.due_date.asc()).limit(3).all()
    return {"chat_id": chat_id, "title": title, "custom_title": space.title, "theme": space.theme, "cover_image": space.cover_image, "created_at": space.created_at, "participants": people, "stats": {"messages": messages, "photos": photos, "memories": memories, "days": max(1, (int(time.time()) - space.created_at) // 86400 + 1)}, "nearest_date": None if not nearest else {"id": nearest.id, "title": nearest.title, "event_date": nearest.event_date}, "plans": [_note_dict(plan) for plan in plans]}

@router.get("/{chat_id}/photos")
def space_photos(chat_id: str, limit: int = 30, offset: int = 0, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    limit = min(max(limit, 1), 100); offset = max(offset, 0)
    rows = db.query(MessageORM).filter(MessageORM.chat_id == chat_id, MessageORM.is_image.is_(True), MessageORM.deleted_at.is_(None)).order_by(MessageORM.created_at.desc()).offset(offset).limit(limit).all()
    import json
    return [{"id": x.id, "created_at": x.created_at, "sender_id": x.sender_id, "images": json.loads(x.images or "[]")} for x in rows]

@router.get("/{chat_id}/memories")
def list_memories(chat_id: str, limit: int = 30, offset: int = 0, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    limit = min(max(limit, 1), 100); offset = max(offset, 0)
    rows = db.query(SpaceMemoryORM, MessageORM, UserORM).join(MessageORM, SpaceMemoryORM.message_id == MessageORM.id).join(UserORM, MessageORM.sender_id == UserORM.id).filter(SpaceMemoryORM.chat_id == chat_id, MessageORM.deleted_at.is_(None)).order_by(SpaceMemoryORM.created_at.desc()).offset(offset).limit(limit).all()
    import json
    return [{"id": memory.id, "message_id": message.id, "content": message.content,
             "images": json.loads(message.images or "[]"), "media": json.loads(message.media) if message.media else None,
             "created_at": message.created_at, "author": _display(author)} for memory, message, author in rows]

@router.post("/{chat_id}/memories/{message_id}", status_code=201)
def save_memory(chat_id: str, message_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    message = db.get(MessageORM, message_id)
    if not message or message.chat_id != chat_id or message.deleted_at:
        raise HTTPException(404, "Message not found in this space")
    existing = db.query(SpaceMemoryORM).filter_by(chat_id=chat_id, message_id=message_id).first()
    if existing: return {"id": existing.id, "saved": True}
    memory = SpaceMemoryORM(chat_id=chat_id, message_id=message_id, saved_by_user_id=current_user.id); db.add(memory); db.commit()
    return {"id": memory.id, "saved": True}

@router.delete("/{chat_id}/memories/{message_id}", status_code=204)
def remove_memory(chat_id: str, message_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    db.query(SpaceMemoryORM).filter_by(chat_id=chat_id, message_id=message_id).delete(); db.commit()

@router.get("/{chat_id}/dates")
def list_dates(chat_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    rows = db.query(SpaceDateORM).filter_by(chat_id=chat_id).order_by(SpaceDateORM.event_date.asc()).all()
    return [_date_dict(x) for x in rows]

@router.post("/{chat_id}/dates", status_code=201)
def create_date(chat_id: str, body: SpaceDateInput, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    item = SpaceDateORM(chat_id=chat_id, title=body.title.strip(), event_date=_validate_iso_date(body.event_date), emoji=body.emoji or "❤️", repeats_yearly=body.repeats_yearly, created_by=current_user.id); db.add(item); db.commit()
    return _date_dict(item)

@router.put("/{chat_id}/dates/{date_id}")
def update_date(chat_id: str, date_id: str, body: SpaceDateInput, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    item = db.query(SpaceDateORM).filter_by(id=date_id, chat_id=chat_id).first()
    if not item: raise HTTPException(404, "Date not found")
    item.title, item.event_date, item.emoji, item.repeats_yearly = body.title.strip(), _validate_iso_date(body.event_date), body.emoji or "❤️", body.repeats_yearly; db.commit()
    return _date_dict(item)

@router.delete("/{chat_id}/dates/{date_id}", status_code=204)
def delete_date(chat_id: str, date_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    if not db.query(SpaceDateORM).filter_by(id=date_id, chat_id=chat_id).delete(): raise HTTPException(404, "Date not found")
    db.commit()

@router.get("/{chat_id}/notes")
def list_notes(chat_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    return [_note_dict(x) for x in db.query(SpaceNoteORM).filter_by(chat_id=chat_id).order_by(SpaceNoteORM.updated_at.desc()).all()]

@router.post("/{chat_id}/notes", status_code=201)
def create_note(chat_id: str, body: SpaceNoteInput, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    if body.note_type not in {"note", "plan"}: raise HTTPException(422, "Unknown note type")
    due = _validate_iso_date(body.due_date) if body.due_date else None; now = int(time.time())
    note = SpaceNoteORM(chat_id=chat_id, title=body.title.strip(), content=body.content, note_type=body.note_type, due_date=due, completed=body.completed, created_by=current_user.id, created_at=now, updated_at=now); db.add(note); db.commit(); return _note_dict(note)

@router.put("/{chat_id}/notes/{note_id}")
def update_note(chat_id: str, note_id: str, body: SpaceNoteInput, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    note = db.query(SpaceNoteORM).filter_by(id=note_id, chat_id=chat_id).first()
    if not note: raise HTTPException(404, "Note not found")
    if body.note_type not in {"note", "plan"}: raise HTTPException(422, "Unknown note type")
    note.title, note.content, note.note_type, note.due_date, note.completed, note.updated_at = body.title.strip(), body.content, body.note_type, _validate_iso_date(body.due_date) if body.due_date else None, body.completed, int(time.time()); db.commit(); return _note_dict(note)

@router.delete("/{chat_id}/notes/{note_id}", status_code=204)
def delete_note(chat_id: str, note_id: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_private_participant(db, chat_id, current_user.id); _space(db, chat_id)
    if not db.query(SpaceNoteORM).filter_by(id=note_id, chat_id=chat_id).delete(): raise HTTPException(404, "Note not found")
    db.commit()
