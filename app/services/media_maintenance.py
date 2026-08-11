"""Conservative, opt-in cleanup for finalized voice/video uploads.

Nothing calls this automatically; operators can first use ``dry_run=True``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from sqlalchemy.orm import Session
from app.core.database import MessageORM


def referenced_media_urls(db: Session) -> set[str]:
    urls: set[str] = set()
    for (raw,) in db.query(MessageORM.media).filter(MessageORM.media.isnot(None)):
        try:
            media = json.loads(raw)
            if isinstance(media, dict):
                urls.update(str(media[key]) for key in ("url", "thumbnail_url") if media.get(key))
        except (TypeError, ValueError):
            continue
    return urls


def orphan_paths(root: Path, referenced: set[str], ttl_seconds: int = 86400, now: float | None = None) -> list[Path]:
    now = time.time() if now is None else now
    candidates: list[Path] = []
    for folder, prefix in ((root / "voice", "/uploads/voice/"), (root / "video_notes", "/uploads/video_notes/")):
        if not folder.exists():
            continue
        for path in folder.iterdir():
            if path.is_file() and now - path.stat().st_mtime >= ttl_seconds and prefix + path.name not in referenced:
                candidates.append(path)
    return candidates


def cleanup_orphan_media(db: Session, root: Path, ttl_seconds: int = 86400, dry_run: bool = True) -> list[Path]:
    candidates = orphan_paths(root, referenced_media_urls(db), ttl_seconds)
    if not dry_run:
        for path in candidates:
            path.unlink(missing_ok=True)
    return candidates
