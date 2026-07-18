import asyncio
import types

import bot
import config


class _FakeFile:
    async def download_to_drive(self, path):
        with open(path, "wb") as fh:
            fh.write(b"%PDF-1.4 or image bytes")


class _FakeBot:
    async def get_file(self, file_id):
        return _FakeFile()


class _FakeMsg:
    def __init__(self, document=None, photo=None, caption=""):
        self.document = document
        self.photo = photo
        self.caption = caption
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self, msg):
        self.message = msg
        self.effective_chat = types.SimpleNamespace(id=1)


def _ctx():
    return types.SimpleNamespace(bot=_FakeBot())


def _doc(name):
    return types.SimpleNamespace(file_name=name, file_id="f", file_unique_id="u")


def _photo():
    return [types.SimpleNamespace(file_id="f", file_unique_id="u")]


def _capture_run_and_reply(monkeypatch):
    seen = {}

    async def fake_rr(update, ctx, prompt, files=None):
        seen["prompt"] = prompt
        seen["files"] = files

    monkeypatch.setattr(bot, "_run_and_reply", fake_rr)
    return seen


def test_pdf_upload_inlines_extracted_text(monkeypatch):
    monkeypatch.setattr(bot, "_allowed", lambda u: True)

    async def fake_pdf(path):
        return "EXTRACTED PDF TEXT"

    monkeypatch.setattr(bot.extract, "extract_pdf", fake_pdf)
    seen = _capture_run_and_reply(monkeypatch)
    msg = _FakeMsg(document=_doc("resume.pdf"))
    asyncio.run(bot.on_document(_FakeUpdate(msg), _ctx()))
    assert "EXTRACTED PDF TEXT" in seen["prompt"]
    assert seen["files"] is None  # no more --file attach for PDFs


def test_pdf_upload_failure_replies_gracefully(monkeypatch):
    monkeypatch.setattr(bot, "_allowed", lambda u: True)

    async def fake_pdf(path):
        return None

    monkeypatch.setattr(bot.extract, "extract_pdf", fake_pdf)
    called = {"rr": False}

    async def fake_rr(*a, **k):
        called["rr"] = True

    monkeypatch.setattr(bot, "_run_and_reply", fake_rr)
    msg = _FakeMsg(document=_doc("resume.pdf"))
    asyncio.run(bot.on_document(_FakeUpdate(msg), _ctx()))
    assert called["rr"] is False
    assert any("couldn't read" in r.lower() for r in msg.replies)


def test_photo_upload_inlines_transcribed_text(monkeypatch):
    monkeypatch.setattr(bot, "_allowed", lambda u: True)

    async def fake_img(path):
        return "TRANSCRIBED PHOTO TEXT"

    monkeypatch.setattr(bot.extract, "extract_image", fake_img)
    seen = _capture_run_and_reply(monkeypatch)
    msg = _FakeMsg(photo=_photo())
    asyncio.run(bot.on_photo(_FakeUpdate(msg), _ctx()))
    assert "TRANSCRIBED PHOTO TEXT" in seen["prompt"]
    assert seen["files"] is None
