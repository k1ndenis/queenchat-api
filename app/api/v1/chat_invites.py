"""Invitations to QueenChat that create only a private chat, never a Space."""
import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import PrivateChatInviteORM, UserORM
from app.core.dependency import get_current_user, get_db
from app.services.chat_service import ChatService

router = APIRouter()
INVITE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_ACTIVE_INVITES = 5


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _state(invite: PrivateChatInviteORM, now: int) -> str:
    if invite.revoked_at: return "revoked"
    if invite.accepted_at: return "used"
    if invite.expires_at <= now: return "expired"
    return "active"


@router.post("", status_code=201)
def create_invite(current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    now = int(time.time())
    active = db.query(func.count(PrivateChatInviteORM.id)).filter(
        PrivateChatInviteORM.creator_user_id == current_user.id,
        PrivateChatInviteORM.accepted_at.is_(None),
        PrivateChatInviteORM.revoked_at.is_(None),
        PrivateChatInviteORM.expires_at > now,
    ).scalar()
    if active >= MAX_ACTIVE_INVITES:
        raise HTTPException(429, "Maximum number of active invitations reached")
    token = secrets.token_urlsafe(32)
    invite = PrivateChatInviteORM(token_hash=_hash(token), creator_user_id=current_user.id, expires_at=now + INVITE_TTL_SECONDS)
    db.add(invite); db.commit(); db.refresh(invite)
    return {"id": invite.id, "invite_url": f"https://queenchat.ru/invite/{token}", "expires_at": invite.expires_at}


@router.get("/{token}/preview")
def preview_invite(token: str, db: Session = Depends(get_db)):
    invite = db.query(PrivateChatInviteORM).filter_by(token_hash=_hash(token)).first()
    if not invite: return {"status": "invalid"}
    state = _state(invite, int(time.time()))
    if state != "active": return {"status": state}
    creator = db.get(UserORM, invite.creator_user_id)
    return {"status": "active", "creator": {"display_name": (creator.display_name or creator.username).strip(), "avatar": creator.avatar}, "expires_at": invite.expires_at}


@router.post("/{token}/accept")
def accept_invite(token: str, current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    invite = db.query(PrivateChatInviteORM).filter_by(token_hash=_hash(token)).with_for_update().first()
    if not invite: raise HTTPException(404, "Invitation not found")
    state = _state(invite, int(time.time()))
    if state != "active": raise HTTPException(409, f"Invitation is {state}")
    if invite.creator_user_id == current_user.id: raise HTTPException(400, "You cannot accept your own invitation")
    db.query(UserORM).filter(UserORM.id == invite.creator_user_id).with_for_update().first()
    service = ChatService(db)
    chat = service.get_existing_private_chat(invite.creator_user_id, current_user.id)
    if not chat:
        chat_response = service.create_chat(None, False, invite.creator_user_id, [invite.creator_user_id, current_user.id], "private")
        chat = service.repo.get_chat(chat_response.id)
    invite.accepted_at, invite.accepted_by_user_id = int(time.time()), current_user.id
    db.commit()
    return {"chat_id": chat.id}
