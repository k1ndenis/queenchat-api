from types import SimpleNamespace

from app.api.v1.chats import _message_preview


def message(**overrides):
    values = {"content": "", "deleted_at": None, "media": None, "is_image": False, "is_sticker": False}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_media_push_preview_is_never_empty():
    assert _message_preview(message(media='{"type":"voice"}')) == "Голосовое сообщение"
    assert _message_preview(message(media='{"type":"video_note"}')) == "Видеосообщение"


def test_text_image_and_sticker_previews_keep_contract():
    assert _message_preview(message(content="Hello")) == "Hello"
    assert _message_preview(message(is_image=True)) == "Image"
    assert _message_preview(message(is_sticker=True)) == "Sticker"
