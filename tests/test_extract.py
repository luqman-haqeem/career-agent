import asyncio
import sys

import extract


# ---- fakes ---------------------------------------------------------------
class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.posted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.posted.append(json)
        return self._resp


def _install_client(monkeypatch, resp):
    client = _FakeClient(resp)
    monkeypatch.setattr(extract.httpx, "AsyncClient", lambda *a, **k: client)
    return client


class _FakePix:
    def tobytes(self, fmt):
        return b"PNGBYTES"


class _FakePage:
    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text

    def get_pixmap(self, dpi=72):
        return _FakePix()


class _FakeDoc:
    def __init__(self, texts):
        self._texts = texts
        self.page_count = len(texts)

    def load_page(self, i):
        return _FakePage(self._texts[i])

    def close(self):
        pass


def _install_fitz(monkeypatch, texts):
    import types
    mod = types.ModuleType("fitz")
    mod.open = lambda path: _FakeDoc(texts)
    monkeypatch.setitem(sys.modules, "fitz", mod)


# ---- _vision_transcribe --------------------------------------------------
def test_vision_transcribe_returns_content(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _install_client(monkeypatch, _FakeResp(
        200, {"choices": [{"message": {"content": "TRANSCRIBED"}}]}))
    out = asyncio.run(extract._vision_transcribe(b"img", "image/png"))
    assert out == "TRANSCRIBED"


def test_vision_transcribe_none_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    out = asyncio.run(extract._vision_transcribe(b"img", "image/png"))
    assert out is None


def test_vision_transcribe_none_on_http_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _install_client(monkeypatch, _FakeResp(500, {}))
    assert asyncio.run(extract._vision_transcribe(b"img", "image/png")) is None


def test_vision_transcribe_none_on_malformed_body(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _install_client(monkeypatch, _FakeResp(200, {"unexpected": True}))
    assert asyncio.run(extract._vision_transcribe(b"img", "image/png")) is None


# ---- extract_pdf ---------------------------------------------------------
def test_extract_pdf_returns_text_without_vision(monkeypatch):
    _install_fitz(monkeypatch, ["Jane Doe\nSenior Engineer at Acme"])
    calls = {"n": 0}

    async def _spy(b, m):
        calls["n"] += 1
        return "SHOULD NOT RUN"

    monkeypatch.setattr(extract, "_vision_transcribe", _spy)
    out = asyncio.run(extract.extract_pdf("x.pdf"))
    assert "Senior Engineer at Acme" in out
    assert calls["n"] == 0


def test_extract_pdf_scanned_uses_vision(monkeypatch):
    _install_fitz(monkeypatch, ["", ""])  # no embedded text

    async def _spy(b, m):
        return "page-text"

    monkeypatch.setattr(extract, "_vision_transcribe", _spy)
    out = asyncio.run(extract.extract_pdf("x.pdf"))
    assert out == "page-text\n\npage-text"


def test_extract_pdf_caps_scanned_pages(monkeypatch):
    _install_fitz(monkeypatch, [""] * 7)
    calls = {"n": 0}

    async def _spy(b, m):
        calls["n"] += 1
        return "t"

    monkeypatch.setattr(extract, "_vision_transcribe", _spy)
    asyncio.run(extract.extract_pdf("x.pdf"))
    assert calls["n"] == extract._MAX_SCAN_PAGES  # 5, not 7


def test_extract_pdf_none_without_fitz(monkeypatch):
    monkeypatch.setitem(sys.modules, "fitz", None)  # import fitz -> ImportError
    assert asyncio.run(extract.extract_pdf("x.pdf")) is None


# ---- extract_image -------------------------------------------------------
def test_extract_image_transcribes_file(monkeypatch, tmp_path):
    p = tmp_path / "resume.png"
    p.write_bytes(b"PNGDATA")
    seen = {}

    async def _spy(data, mime):
        seen["data"] = data
        seen["mime"] = mime
        return "img-text"

    monkeypatch.setattr(extract, "_vision_transcribe", _spy)
    out = asyncio.run(extract.extract_image(p))
    assert out == "img-text"
    assert seen["data"] == b"PNGDATA"
    assert seen["mime"] == "image/png"


def test_extract_image_none_on_unreadable(monkeypatch, tmp_path):
    assert asyncio.run(extract.extract_image(tmp_path / "missing.jpg")) is None
