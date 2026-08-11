import os
import time
from pathlib import Path
from app.services.media_maintenance import orphan_paths

def test_orphan_selection_respects_references_and_ttl(tmp_path):
    root = tmp_path; (root / "voice").mkdir(); (root / "video_notes").mkdir()
    files = {name: root / "voice" / name for name in ("a.ogg", "b.ogg", "c.ogg", "d.ogg")}
    for path in files.values(): path.write_bytes(b"x")
    now = time.time()
    for key in ("a.ogg", "b.ogg", "d.ogg"): os.utime(files[key], (now - 90000, now - 90000))
    selected = orphan_paths(root, {"/uploads/voice/a.ogg", "/uploads/voice/b.ogg"}, now=now)
    assert selected == [files["d.ogg"]]

def test_orphan_selection_keeps_video_and_thumbnail_references(tmp_path):
    root = tmp_path; directory = root / "video_notes"; directory.mkdir()
    keep_mp4, keep_jpg, old_mp4, old_jpg = [directory / name for name in ("keep.mp4", "keep.jpg", "old.mp4", "old.jpg")]
    for path in (keep_mp4, keep_jpg, old_mp4, old_jpg): path.write_bytes(b"x")
    now = time.time()
    for path in (keep_mp4, keep_jpg, old_mp4, old_jpg): os.utime(path, (now - 90000, now - 90000))
    selected = orphan_paths(root, {"/uploads/video_notes/keep.mp4", "/uploads/video_notes/keep.jpg"}, now=now)
    assert set(selected) == {old_mp4, old_jpg}
