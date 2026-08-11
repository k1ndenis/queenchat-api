from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import List, Optional
from sqlalchemy.orm import Session
from pathlib import Path
import os
import asyncio
import tempfile
import struct
import uuid
import json
import logging
import math

from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.services.chat_service import ChatService

router = APIRouter()
logger = logging.getLogger(__name__)

UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", "/app/uploads")).resolve()
UPLOAD_DIR = UPLOAD_ROOT / "images"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_FILES = 10
VOICE_DIR = UPLOAD_ROOT / 'voice'; VIDEO_DIR = UPLOAD_ROOT / 'video_notes'; TMP_DIR = UPLOAD_ROOT / 'tmp'
for directory in (VOICE_DIR, VIDEO_DIR, TMP_DIR): directory.mkdir(parents=True, exist_ok=True)
VOICE_LIMIT, VIDEO_LIMIT = 25 * 1024 * 1024, 100 * 1024 * 1024
# The VPS has limited RAM; H.264 transcoding is deliberately serialized while
# inexpensive voice normalization may run in parallel.
voice_transcode_semaphore = asyncio.Semaphore(2)
video_transcode_semaphore = asyncio.Semaphore(1)

async def _run(*args: str, timeout: int = 120) -> bytes:
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try: stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill(); await process.communicate(); raise HTTPException(422, detail='Media processing timed out')
    if process.returncode:
        logger.warning("Media command failed: tool=%s exit=%s stderr=%s", args[0], process.returncode, stderr.decode(errors='replace')[-500:])
        raise HTTPException(422, detail='Unsupported or invalid media file')
    return stdout

async def _duration(path: Path) -> float:
    raw = await _run('ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path), timeout=20)
    try:
        probe = json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(422, detail='Unable to read media duration')

    format_data = probe.get('format') or {}
    streams = probe.get('streams') or []
    logger.warning(
        "[MediaProbe] format_name=%s format_duration=%s streams=%s",
        format_data.get('format_name'), format_data.get('duration'),
        [{key: stream.get(key) for key in ('codec_type', 'codec_name', 'duration', 'start_time')} for stream in streams],
    )

    def finite_number(value) -> float | None:
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    def positive_number(value) -> float | None:
        number = finite_number(value)
        return number if number is not None and number > 0 else None

    duration = positive_number(format_data.get('duration'))
    if duration is not None:
        return duration
    stream_durations = [value for stream in streams if (value := positive_number(stream.get('duration'))) is not None]
    if stream_durations:
        return max(stream_durations)

    # Some valid WebM recordings omit Duration metadata. Only after ffprobe has
    # identified an actual audio/video stream, derive a duration from real packet
    # timestamps near EOF; no client-provided duration is ever used.
    media_streams = [stream for stream in streams if stream.get('codec_type') in {'audio', 'video'} and stream.get('codec_name')]
    if not media_streams:
        raise HTTPException(422, detail='Unable to read media duration')
    packets_raw = await _run('ffprobe', '-v', 'error', '-show_packets', '-show_entries', 'packet=stream_index,pts_time,dts_time,duration_time', '-of', 'json', str(path), timeout=20)
    try:
        packets = json.loads(packets_raw).get('packets') or []
    except (ValueError, TypeError):
        packets = []
    stream_types = {stream.get('index'): stream.get('codec_type') for stream in media_streams}
    packet_counts = {'video': 0, 'audio': 0}
    starts, ends = [], []
    for packet in packets:
        stream_type = stream_types.get(packet.get('stream_index'))
        if stream_type not in packet_counts:
            continue
        timestamp = finite_number(packet.get('pts_time'))
        if timestamp is None:
            timestamp = finite_number(packet.get('dts_time'))
        if timestamp is None:
            continue
        packet_duration = finite_number(packet.get('duration_time'))
        packet_counts[stream_type] += 1
        starts.append(timestamp)
        ends.append(timestamp + max(packet_duration or 0, 0))
    if starts and ends:
        duration = max(ends) - min(starts)
        logger.warning(
            "[MediaProbe] packets: video_packets=%s audio_packets=%s first_timestamp=%s last_timestamp=%s derived_duration=%s",
            packet_counts['video'], packet_counts['audio'], min(starts), max(ends), duration,
        )
        if math.isfinite(duration) and duration > 0:
            return duration
    raise HTTPException(422, detail='Unable to read media duration')

async def _waveform(path: Path, samples: int = 64) -> list[float]:
    try:
        raw = await _run('ffmpeg', '-v', 'error', '-i', str(path), '-ac', '1', '-ar', '8000', '-f', 's16le', 'pipe:1', timeout=20)
        values = struct.unpack(f'<{len(raw)//2}h', raw[:len(raw)//2*2])
        if not values: return []
        step = max(1, len(values) // samples); peaks = [max(abs(value) for value in values[i:i+step]) for i in range(0, len(values), step)][:samples]
        maximum = max(peaks) or 1
        return [round(peak / maximum, 3) for peak in peaks]
    except Exception: return []

async def _save_upload(file: UploadFile, limit: int) -> Path:
    path = TMP_DIR / uuid.uuid4().hex
    size = 0
    try:
        with open(path, 'wb') as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit: raise HTTPException(413, detail='Media file is too large')
                target.write(chunk)
        return path
    except Exception:
        path.unlink(missing_ok=True); raise

@router.post('/upload-voice')
async def upload_voice(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    logger.warning("[MediaUpload] voice entered filename=%s content_type=%s", file.filename, file.content_type)
    source = None
    final = VOICE_DIR / f'{uuid.uuid4().hex}.ogg'; partial = final.with_name(f'.{final.stem}.part.ogg')
    completed = False
    try:
        source = await _save_upload(file, VOICE_LIMIT)
        logger.warning("[MediaUpload] voice saved path=%s size=%s", source, source.stat().st_size)
        async with voice_transcode_semaphore:
            logger.warning("[MediaUpload] voice probing")
            duration = await _duration(source)
            logger.warning("[MediaUpload] voice duration=%s", duration)
            if duration <= 0 or duration > 300: raise HTTPException(422, detail='Voice message must be at most 5 minutes')
            logger.warning("[MediaUpload] voice transcoding")
            await _run('ffmpeg','-y','-i',str(source),'-vn','-ac','1','-c:a','libopus','-b:a','40k',str(partial), timeout=60)
            logger.warning("[MediaUpload] voice transcode ok")
            partial.replace(final); completed = True
        return {'url': f'/uploads/voice/{final.name}', 'duration': round(duration, 1), 'file_size': final.stat().st_size, 'mime_type':'audio/ogg', 'waveform': await _waveform(final)}
    except HTTPException as exc:
        logger.warning("[MediaUpload] voice failed status=%s detail=%s", exc.status_code, exc.detail)
        raise
    except Exception:
        logger.exception("[MediaUpload] voice unexpected failure")
        raise
    finally:
        if source is not None: source.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        if not completed: final.unlink(missing_ok=True)

@router.post('/upload-video-note')
async def upload_video_note(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    logger.warning("[MediaUpload] video_note entered filename=%s content_type=%s", file.filename, file.content_type)
    source = None
    stem = uuid.uuid4().hex; final = VIDEO_DIR / f'{stem}.mp4'; thumb = VIDEO_DIR / f'{stem}.jpg'; partial = VIDEO_DIR / f'.{stem}.part.mp4'; thumb_partial = VIDEO_DIR / f'.{stem}.part.jpg'
    completed = False
    try:
        source = await _save_upload(file, VIDEO_LIMIT)
        logger.warning("[MediaUpload] video_note saved path=%s size=%s", source, source.stat().st_size)
        async with video_transcode_semaphore:
            logger.warning("[MediaUpload] video_note probing")
            duration = await _duration(source)
            logger.warning("[MediaUpload] video_note duration=%s", duration)
            if duration <= 0 or duration > 60: raise HTTPException(422, detail='Video note must be at most 60 seconds')
            vf = "crop='min(iw,ih)':'min(iw,ih)',scale=480:480:force_original_aspect_ratio=decrease,pad=480:480:(ow-iw)/2:(oh-ih)/2"
            logger.warning("[MediaUpload] video_note transcoding")
            await _run('ffmpeg','-y','-i',str(source),'-t','60','-vf',vf,'-c:v','libx264','-preset','medium','-crf','26','-maxrate','900k','-bufsize','1800k','-c:a','aac','-b:a','64k','-movflags','+faststart',str(partial), timeout=120)
            logger.warning("[MediaUpload] video_note transcode ok")
            await _run('ffmpeg','-y','-ss','0.1','-i',str(partial),'-frames:v','1','-q:v','4',str(thumb_partial), timeout=30)
            partial.replace(final); thumb_partial.replace(thumb); completed = True
        return {'url':f'/uploads/video_notes/{final.name}','duration':round(duration,1),'width':480,'height':480,'thumbnail_url':f'/uploads/video_notes/{thumb.name}','file_size':final.stat().st_size,'mime_type':'video/mp4'}
    except HTTPException as exc:
        logger.warning("[MediaUpload] video_note failed status=%s detail=%s", exc.status_code, exc.detail)
        raise
    except Exception:
        logger.exception("[MediaUpload] video_note unexpected failure")
        raise
    finally:
        if source is not None: source.unlink(missing_ok=True)
        partial.unlink(missing_ok=True); thumb_partial.unlink(missing_ok=True)
        if not completed: final.unlink(missing_ok=True); thumb.unlink(missing_ok=True)

@router.post("/upload-images")
async def upload_images(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES} files allowed")
    
    uploaded_urls = []
    errors = []
    
    for file in files:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            errors.append(f"{file.filename}: unsupported file type")
            continue
        
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
        if size > MAX_FILE_SIZE:
            errors.append(f"{file.filename}: file too large (max 10MB)")
            continue
        
        new_filename = f"{uuid.uuid4().hex}{ext}"
        file_path = UPLOAD_DIR / new_filename
        
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        
        file_url = f"/uploads/images/{new_filename}"
        uploaded_urls.append(file_url)
    
    return {
        "success": True,
        "urls": uploaded_urls,
        "errors": errors if errors else None,
        "count": len(uploaded_urls)
    }

@router.post("/upload-avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    
    ext = os.path.splitext(file.filename)[1].lower()
    new_filename = f"avatar_{current_user.id}{ext}"
    file_path = UPLOAD_DIR / new_filename
    
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    
    avatar_url = f"/uploads/images/{new_filename}"
    
    from app.services.auth_service import AuthService
    auth_service = AuthService(db)
    auth_service.update_profile(
        user_id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        avatar=avatar_url
    )
    db.commit()
    
    return {"success": True, "url": avatar_url}

@router.post("/upload-chat-avatar")
async def upload_chat_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    
    ext = os.path.splitext(file.filename)[1].lower()
    new_filename = f"chat_avatar_{uuid.uuid4().hex}{ext}"
    file_path = UPLOAD_DIR / new_filename
    
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    
    avatar_url = f"/uploads/images/{new_filename}"
    
    return {"success": True, "url": avatar_url}


@router.post("/upload-chat-background")
async def upload_chat_background(
    chat_id: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chat_service = ChatService(db)
    chat = chat_service.repo.get_chat(chat_id)
    if not chat or not chat_service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="You are not a participant of this chat")
    if chat.chat_type != "private" and chat.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only the chat creator can change this background")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    new_filename = f"chat_background_{chat_id}_{uuid.uuid4().hex}{ext}"
    with open(UPLOAD_DIR / new_filename, "wb") as destination:
        destination.write(await file.read())
    return {"success": True, "url": f"/uploads/images/{new_filename}"}
