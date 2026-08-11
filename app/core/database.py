from sqlalchemy import create_engine, String, ForeignKey, Integer, Boolean, Text, UniqueConstraint, CheckConstraint, JSON
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column, relationship
import uuid
import os
import time
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import FastAPI
from typing import Optional, List

from app.core.redis import redis_client

load_dotenv()

TESTING = os.getenv("TESTING") == "true"

if TESTING:
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
    # A test process must never silently attach to the deployed PostgreSQL DB.
    if not DATABASE_URL.startswith("sqlite:") or "queenchat_test" not in DATABASE_URL and "/tmp/" not in DATABASE_URL:
        raise RuntimeError("TESTING requires an isolated SQLite DATABASE_URL under /tmp or named queenchat_test")
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT")
    DB_NAME = os.getenv("DB_NAME")
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class UserORM(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),)
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    avatar: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[int] = mapped_column(nullable=False)
    # Kept as a validated string rather than a PostgreSQL enum so this remains
    # compatible with the project's SQLite test database.
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user", server_default="user", index=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false", index=True)
    blocked_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    blocked_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    chats: Mapped[List["ChatORM"]] = relationship(
        secondary="chat_participants",
        back_populates="participants"
    )
    messages: Mapped[List["MessageORM"]] = relationship(
        "MessageORM", 
        foreign_keys="[MessageORM.sender_id]",
        overlaps="sender"
    )


class ChatORM(Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_group: Mapped[bool] = mapped_column(default=False)
    chat_type: Mapped[str] = mapped_column(String, default="private")
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(default=lambda: int(time.time()), onupdate=lambda: int(time.time()))
    
    participants: Mapped[List["UserORM"]] = relationship(
        secondary="chat_participants",
        back_populates="chats"
    )
    messages: Mapped[List["MessageORM"]] = relationship(
        "MessageORM",
        back_populates="chat",
        cascade="all, delete-orphan"
    )


class ChatParticipantORM(Base):
    __tablename__ = "chat_participants"
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    joined_at: Mapped[int] = mapped_column(default=lambda: int(time.time()))


class ChatBackgroundPreferenceORM(Base):
    __tablename__ = "chat_background_preferences"

    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    background_type: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    background_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))
    updated_by_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PrivateSpaceSettingsORM(Base):
    __tablename__ = "private_space_settings"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # enabled is kept for compatibility with the first Spaces release.  New
    # code must use status: a pair space is usable only after acceptance.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    title: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    theme: Mapped[str] = mapped_column(String(24), nullable=False, default="queen", server_default="queen")
    accent: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    cover_image: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))


class PrivateSpaceInviteORM(Base):
    __tablename__ = "private_space_invites"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    creator_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    recipient_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    chat_id: Mapped[Optional[str]] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"), index=True, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))
    expires_at: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    accepted_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    accepted_by_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class SpaceMemoryORM(Base):
    __tablename__ = "space_memories"
    __table_args__ = (UniqueConstraint("chat_id", "message_id", name="uq_space_memories_chat_message"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=False)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False)
    saved_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))


class SpaceDateORM(Base):
    __tablename__ = "space_dates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    event_date: Mapped[str] = mapped_column(String(10), nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), nullable=False, default="❤️", server_default="❤️")
    repeats_yearly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))


class SpaceNoteORM(Base):
    __tablename__ = "space_notes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note_type: Mapped[str] = mapped_column(String(12), nullable=False, default="note", server_default="note")
    due_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))


class MessageORM(Base):
    __tablename__ = "messages"
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"))
    sender_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    content: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sticker_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_sticker: Mapped[bool] = mapped_column(Boolean, default=False)
    is_image: Mapped[bool] = mapped_column(Boolean, default=False)
    images: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reply_to_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("messages.id"), nullable=True)
    forwarded_from_message_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("messages.id"), nullable=True)
    forwarded_from_user_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))
    edited_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    
    chat: Mapped["ChatORM"] = relationship("ChatORM", back_populates="messages")
    sender: Mapped["UserORM"] = relationship(
        "UserORM", 
        foreign_keys=[sender_id],
        overlaps="messages"
    )
    reply_to: Mapped[Optional["MessageORM"]] = relationship(
        "MessageORM", 
        remote_side=[id], 
        foreign_keys=[reply_to_id]
    )
    forwarded_from_message: Mapped[Optional["MessageORM"]] = relationship(
        "MessageORM",
        remote_side=[id],
        foreign_keys=[forwarded_from_message_id]
    )
    forwarded_from_user: Mapped[Optional["UserORM"]] = relationship(
        "UserORM",
        foreign_keys=[forwarded_from_user_id]
    )
    replies: Mapped[List["MessageORM"]] = relationship(
        "MessageORM", 
        foreign_keys=[reply_to_id],
        overlaps="reply_to"
    )
    reactions: Mapped[List["MessageReactionORM"]] = relationship(
        "MessageReactionORM",
        back_populates="message",
        cascade="all, delete-orphan"
    )


class MessageReactionORM(Base):
    __tablename__ = "message_reactions"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_reactions_message_user"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()), nullable=False)

    message: Mapped["MessageORM"] = relationship("MessageORM", back_populates="reactions")
    user: Mapped["UserORM"] = relationship("UserORM")


class ReactionNotificationORM(Base):
    __tablename__ = "reaction_notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "message_id", "reaction_user_id", name="uq_reaction_notifications_target_message_reactor"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=False)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False)
    reaction_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()), nullable=False)
    read_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

class MessageCommentORM(Base):
    __tablename__ = "message_comments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False)
    channel_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()), nullable=False)
    edited_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user: Mapped["UserORM"] = relationship("UserORM")


class FileORM(Base):
    __tablename__ = "files"
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String, nullable=False)
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    chat_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("chats.id"), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()))
    
    user: Mapped["UserORM"] = relationship("UserORM", back_populates="files")
    chat: Mapped[Optional["ChatORM"]] = relationship("ChatORM", back_populates="files")


# Добавляем отношения после определения всех классов
UserORM.files = relationship("FileORM", back_populates="user", cascade="all, delete-orphan")
ChatORM.files = relationship("FileORM", back_populates="chat", cascade="all, delete-orphan")


class AdminAuditLogORM(Base):
    __tablename__ = "admin_audit_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    admin_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(time.time()), index=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    
    if not TESTING:
        try:
            with engine.connect() as conn:
                conn.execute(Text("ALTER TABLE chats ADD COLUMN chat_type VARCHAR(20) DEFAULT 'private'"))
                conn.commit()
                print("✅ Added chat_type column to chats table")
        except Exception as e:
            # Column already exists
            pass
    
    if not TESTING:
        try:
            with engine.connect() as conn:
                conn.execute(Text("ALTER TABLE chats ADD COLUMN avatar TEXT"))
                conn.commit()
                print("✅ Added avatar column to chats table")
        except Exception as e:
            pass
    
    if not TESTING:
        try:
            redis_client.ping()
            print("✅ Redis connected")
        except Exception as e:
            print(f"❌ Redis connection failed: {e}")
    
    yield
    
    if not TESTING:
        redis_client.close()
        print("Redis connection closed")
