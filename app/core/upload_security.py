import logging
import os
import shutil
import time
from pathlib import Path

from fastapi import HTTPException
from app.core.redis import redis_client
from app.core.rate_limit import safe_key

logger = logging.getLogger(__name__)

MIN_FREE_DISK_PERCENT = float(os.getenv("UPLOAD_MIN_FREE_DISK_PERCENT", "10"))
DAILY_UPLOAD_BYTES = int(os.getenv("DAILY_UPLOAD_BYTES_PER_USER", str(500 * 1024 * 1024)))
DAILY_UPLOAD_FILES = int(os.getenv("DAILY_UPLOAD_FILES_PER_USER", "100"))


def ensure_disk_capacity(path: Path) -> None:
    usage = shutil.disk_usage(path)
    free_percent = usage.free * 100 / usage.total
    if free_percent < MIN_FREE_DISK_PERCENT:
        logger.warning("UPLOAD_REJECTED reason=disk_low free_percent=%.2f", free_percent)
        raise HTTPException(503, "Upload storage is temporarily unavailable")


def enforce_daily_quota(user_id: str, byte_count: int) -> None:
    day = time.strftime("%Y%m%d", time.gmtime())
    base = f"queenchat:upload:{day}:{safe_key(user_id)}"
    try:
        bytes_used = int(redis_client.incrby(base + ":bytes", byte_count))
        files_used = int(redis_client.incr(base + ":files"))
        if bytes_used == byte_count:
            redis_client.expire(base + ":bytes", 86400)
        if files_used == 1:
            redis_client.expire(base + ":files", 86400)
        if bytes_used > DAILY_UPLOAD_BYTES or files_used > DAILY_UPLOAD_FILES:
            logger.warning("UPLOAD_REJECTED reason=daily_quota user_id=%s", user_id)
            raise HTTPException(429, "Daily upload quota reached. Please try again tomorrow.")
    except HTTPException:
        raise
    except Exception:
        logger.exception("upload quota unavailable")


def image_extension_and_signature(filename: str | None, content: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    signatures = {
        ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
        ".png": (b"\x89PNG\r\n\x1a\n",), ".gif": (b"GIF87a", b"GIF89a"),
        ".webp": (b"RIFF",), ".bmp": (b"BM",),
    }
    if ext not in signatures or not any(content.startswith(sig) for sig in signatures[ext]):
        logger.warning("UPLOAD_REJECTED reason=invalid_image_signature extension=%s", ext)
        raise HTTPException(422, "Unsupported or invalid image file")
    if ext == ".webp" and content[8:12] != b"WEBP":
        raise HTTPException(422, "Unsupported or invalid image file")
    return ext
