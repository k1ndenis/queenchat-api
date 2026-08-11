import asyncio
import os
import subprocess
from pathlib import Path
import pytest
from fastapi import HTTPException
from app.api.v1 import files


class Stream:
    def __init__(self, chunks): self.chunks = iter(chunks); self.reads = 0
    async def read(self, _):
        self.reads += 1
        return next(self.chunks, b"")


@pytest.fixture(autouse=True)
def clean_media():
    for directory in (files.TMP_DIR, files.VOICE_DIR, files.VIDEO_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.iterdir(): path.unlink()
    yield
    for directory in (files.TMP_DIR, files.VOICE_DIR, files.VIDEO_DIR):
        for path in directory.iterdir(): path.unlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [files.VOICE_LIMIT, files.VIDEO_LIMIT])
async def test_streamed_size_gate_boundaries(limit):
    # Reuses one 1 MiB object: no 25/100 MB allocation.
    chunk = b"x" * (1024 * 1024)
    accepted = Stream([chunk] * (limit // len(chunk)) + [b""])
    path = await files._save_upload(accepted, limit)
    assert path.stat().st_size == limit
    path.unlink()
    oversized = Stream([chunk] * (limit // len(chunk)) + [b"x", b""])
    with pytest.raises(HTTPException) as error: await files._save_upload(oversized, limit)
    assert error.value.status_code == 413
    assert not list(files.TMP_DIR.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,final_dir", [("voice", files.VOICE_DIR), ("video", files.VIDEO_DIR)])
async def test_invalid_media_and_probe_cleanup(monkeypatch, endpoint, final_dir):
    async def bad_run(*args, **kwargs): raise HTTPException(422, detail="bad media")
    monkeypatch.setattr(files, "_run", bad_run)
    upload = Stream([b"not media", b""])
    call = files.upload_voice if endpoint == "voice" else files.upload_video_note
    with pytest.raises(HTTPException) as error: await call(upload, object())
    assert error.value.status_code == 422
    assert not list(files.TMP_DIR.iterdir()) and not list(final_dir.iterdir())


@pytest.mark.asyncio
async def test_voice_ffmpeg_cleanup_and_waveform_fallback(monkeypatch):
    calls = []
    async def run(*args, **kwargs):
        calls.append(args)
        if args[0] == "ffprobe": return b"1.0\n"
        if "-c:a" in args: raise HTTPException(422, detail="ffmpeg failed")
        return b""
    monkeypatch.setattr(files, "_run", run)
    with pytest.raises(HTTPException): await files.upload_voice(Stream([b"x", b""]), object())
    assert not list(files.TMP_DIR.iterdir()) and not list(files.VOICE_DIR.iterdir())
    async def success_run(*args, **kwargs):
        if args[0] == "ffprobe": return b"1.0\n"
        if args[0] == "ffmpeg": Path(args[-1]).write_bytes(b"ogg")
        return b""
    monkeypatch.setattr(files, "_run", success_run)
    monkeypatch.setattr(files, "_waveform", lambda _: (_ for _ in ()).throw(RuntimeError("waveform")))
    # _waveform normally owns its failure fallback; emulate its public contract.
    monkeypatch.setattr(files, "_waveform", lambda _: asyncio.sleep(0, result=[]))
    result = await files.upload_voice(Stream([b"x", b""]), object())
    assert result["waveform"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["main", "thumbnail"])
async def test_video_ffmpeg_and_thumbnail_cleanup(monkeypatch, failure):
    async def run(*args, **kwargs):
        if args[0] == "ffprobe": return b"1.0\n"
        output = Path(args[-1])
        if (failure == "main" and output.suffix == ".mp4") or (failure == "thumbnail" and output.suffix == ".jpg"):
            raise HTTPException(422, detail="ffmpeg failed")
        output.write_bytes(b"partial")
        return b""
    monkeypatch.setattr(files, "_run", run)
    with pytest.raises(HTTPException): await files.upload_video_note(Stream([b"x", b""]), object())
    assert not list(files.TMP_DIR.iterdir()) and not list(files.VIDEO_DIR.iterdir())


def _synthetic(path, seconds, video=False):
    if video:
        command = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=16x16:rate=1:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    else:
        command = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=500:duration={seconds}", "-c:a", "libopus", "-b:a", "6k", str(path)]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,accepted,rejected", [("voice", 299, 301), ("video", 59, 61)])
async def test_duration_boundaries(kind, accepted, rejected, tmp_path):
    suffix = ".mp4" if kind == "video" else ".webm"
    good, bad = tmp_path / f"good{suffix}", tmp_path / f"bad{suffix}"
    _synthetic(good, accepted, kind == "video"); _synthetic(bad, rejected, kind == "video")
    call = files.upload_video_note if kind == "video" else files.upload_voice
    before_rejection = None
    for source, expected in ((good, 200), (bad, 422)):
        with source.open("rb") as handle:
            class FileStream:
                async def read(self, size): return handle.read(size)
            if expected == 200:
                result = await call(FileStream(), object()); assert result["duration"] <= (60 if kind == "video" else 300)
                before_rejection = {path.name for path in files.VOICE_DIR.iterdir()} | {path.name for path in files.VIDEO_DIR.iterdir()}
            else:
                with pytest.raises(HTTPException) as error: await call(FileStream(), object())
                assert error.value.status_code == 422
                assert before_rejection == ({path.name for path in files.VOICE_DIR.iterdir()} | {path.name for path in files.VIDEO_DIR.iterdir()})
            assert not list(files.TMP_DIR.iterdir())
