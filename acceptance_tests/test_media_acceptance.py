import os
import subprocess
import asyncio
import json
from pathlib import Path
import pytest

os.environ.update(TESTING="true", DATABASE_URL="sqlite:////tmp/queenchat-tests/acceptance.db", UPLOAD_ROOT="/tmp/queenchat-tests/acceptance-uploads", JWT_SECRET_KEY="queenchat-test-secret-at-least-32-bytes")

from fastapi.testclient import TestClient
from app.core.database import Base, engine
from main import app
from app.api.v1 import files as media_files


def make_media(path: Path, video=False, duration=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=700:duration={duration}"]
    if video:
        command = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=64x64:rate=5:duration={duration}", "-f", "lavfi", "-i", f"sine=frequency=700:duration={duration}", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    else:
        command += ["-c:a", "libopus"]
    subprocess.run(command + [str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def register(client, n):
    result = client.post("/api/auth/register", json={"phone": f"+15550000{n}", "username": f"media{n}", "password": "123456"})
    assert result.status_code == 200, result.text
    return result.cookies["access_token"]


def packet_probe(format_duration=None, stream_duration=None, packets=None, streams=None):
    streams = streams if streams is not None else [
        {"index": 0, "codec_type": "video", "codec_name": "vp8", "duration": stream_duration},
        {"index": 1, "codec_type": "audio", "codec_name": "opus", "duration": stream_duration, "start_time": "-0.001"},
    ]
    metadata = json.dumps({"format": {"format_name": "matroska,webm", "duration": format_duration}, "streams": streams}).encode()
    packets_data = json.dumps({"packets": packets or []}).encode()

    async def fake_run(*args, **kwargs):
        assert "-sseof" not in args
        return packets_data if "-show_packets" in args else metadata
    return fake_run


def test_duration_falls_back_to_real_webm_packet_timestamps(monkeypatch, tmp_path):
    monkeypatch.setattr(media_files, "_run", packet_probe(packets=[
        {"stream_index": 1, "pts_time": "-0.001", "duration_time": "0.020"},
        {"stream_index": 0, "pts_time": "0.000", "duration_time": "0.040"},
        {"stream_index": 1, "pts_time": "12.980", "duration_time": "0.020"},
        {"stream_index": 0, "pts_time": "12.960", "duration_time": "0.040"},
    ]))
    assert asyncio.run(media_files._duration(tmp_path / "video.webm")) == pytest.approx(13.001)


def test_duration_rejects_missing_media_packets(monkeypatch, tmp_path):
    monkeypatch.setattr(media_files, "_run", packet_probe(packets=[]))
    with pytest.raises(Exception) as error:
        asyncio.run(media_files._duration(tmp_path / "empty.webm"))
    assert error.value.status_code == 422


def test_duration_rejects_no_audio_or_video_stream(monkeypatch, tmp_path):
    monkeypatch.setattr(media_files, "_run", packet_probe(streams=[{"index": 0, "codec_type": "subtitle", "codec_name": "webvtt"}]))
    with pytest.raises(Exception) as error:
        asyncio.run(media_files._duration(tmp_path / "subtitle.webm"))
    assert error.value.status_code == 422


def test_video_endpoint_enforces_limit_for_packet_derived_duration(monkeypatch):
    monkeypatch.setattr(media_files, "_run", packet_probe(packets=[
        {"stream_index": 0, "pts_time": "0", "duration_time": "0.04"},
        {"stream_index": 1, "pts_time": "61", "duration_time": "0.02"},
    ]))
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    with TestClient(app) as client:
        client.cookies.set("access_token", register(client, 9))
        response = client.post("/api/files/upload-video-note", files={"file": ("video.webm", b"packet-duration-test", "video/webm")})
    assert response.status_code == 422
    assert response.json()["detail"] == "Video note must be at most 60 seconds"


def test_authenticated_voice_video_create_history_and_range():
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    source = Path("/tmp/queenchat-tests/source")
    voice, video = source / "voice.webm", source / "video.mp4"
    make_media(voice); make_media(video, video=True)
    with TestClient(app) as client:
        token_a, token_b = register(client, 1), register(client, 2)
        client.cookies.set("access_token", token_a)
        chat = client.post("/api/chats/", json={"is_group": False, "participant_ids": ["media2"]})
        assert chat.status_code == 201, chat.text
        chat_id = chat.json()["id"]
        with voice.open("rb") as f: voice_upload = client.post("/api/files/upload-voice", files={"file": ("voice.webm", f, "audio/webm")})
        assert voice_upload.status_code == 200, voice_upload.text
        voice_media = {"type": "voice", **voice_upload.json()}
        assert voice_media["mime_type"] == "audio/ogg" and voice_media["file_size"] > 0
        assert 0 < voice_media["duration"] <= 300 and 1 <= len(voice_media["waveform"]) <= 64
        assert all(0 <= x <= 1 for x in voice_media["waveform"])
        with video.open("rb") as f: video_upload = client.post("/api/files/upload-video-note", files={"file": ("video.mp4", f, "video/mp4")})
        assert video_upload.status_code == 200, video_upload.text
        video_media = {"type": "video_note", **video_upload.json()}
        assert video_media["width"] == video_media["height"] == 480 and video_media["file_size"] > 0
        # Both sockets share this one TestClient lifespan; POST broadcasts to B.
        with client.websocket_connect(f"/api/chats/ws/{chat_id}?token={token_a}"), client.websocket_connect(f"/api/chats/ws/{chat_id}?token={token_b}") as ws_b:
            voice_message = client.post(f"/api/chats/{chat_id}/messages", json={"media": voice_media, "content": ""})
            assert voice_message.status_code == 200
            voice_event = ws_b.receive_json()
            assert voice_event["type"] == "new_message" and voice_event["message"]["media"] == voice_media
            video_message = client.post(f"/api/chats/{chat_id}/messages", json={"media": video_media, "content": ""})
            assert video_message.status_code == 200
            video_event = ws_b.receive_json()
            assert video_event["type"] == "new_message" and video_event["message"]["media"] == video_media
        history = client.get(f"/api/chats/{chat_id}/messages").json()
        assert {m["media"]["url"] for m in history if m["media"]} == {voice_media["url"], video_media["url"]}
        static = client.get(voice_media["url"], headers={"Range": "bytes=0-1023"})
        assert static.status_code == 206 and static.headers["accept-ranges"] == "bytes" and "bytes 0-" in static.headers["content-range"]
        voice_id, video_id = voice_message.json()["id"], video_message.json()["id"]
        reply = client.post(f"/api/chats/{chat_id}/messages", json={"content": "reply", "reply_to_id": voice_id})
        assert reply.status_code == 200 and reply.json()["reply_to_id"] == voice_id
        assert client.put(f"/api/chats/{chat_id}/messages/{voice_id}/reaction", json={"emoji": "👍"}).status_code == 200
        assert client.delete(f"/api/chats/{chat_id}/messages/{voice_id}/reaction").status_code == 200
        assert client.delete(f"/api/chats/{chat_id}/messages/{video_id}").status_code == 200
        # Forward only copies metadata: no upload/transcode endpoint is invoked.
        client.cookies.set("access_token", token_b)
        target = client.post("/api/chats/", json={"is_group": False, "participant_ids": ["media1"]})
        assert target.status_code == 201
        forwarded = client.post(f"/api/chats/{chat_id}/messages/{voice_message.json()['id']}/forward", json={"target_chat_id": target.json()["id"]})
        assert forwarded.status_code == 200 and forwarded.json()["media"]["url"] == voice_media["url"]
