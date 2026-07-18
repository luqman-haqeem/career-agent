"""Extract text from uploaded PDFs and images, server-side.

OpenCode's --file forwarding for binary uploads (PDF, images) does not reliably
reach the model, so we extract text ourselves and inline it into the prompt —
the same approach bot._docx_to_text uses. Text-based PDFs are read with PyMuPDF;
images and scanned (no-text) PDFs are transcribed by a vision model via a direct
OpenRouter call.

Every function degrades to None on any failure (missing library, missing API
key, API error, unreadable file) so an upload never crashes the bot.
"""
import base64
import logging
import os

import httpx

import config

log = logging.getLogger(__name__)

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT = 60.0
_MAX_SCAN_PAGES = 5           # cap vision calls on multi-page scanned PDFs
_MIN_PDF_TEXT = 20            # < this many chars => treat PDF as scanned

_TRANSCRIBE = (
    "Transcribe every piece of text in this document image faithfully to "
    "Markdown. Preserve headings, bullet lists, and the order of content. Do "
    "NOT summarize, infer, translate, or invent anything that is not visibly "
    "present. Output only the transcription."
)

_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


async def _vision_transcribe(image_bytes: bytes, mime: str) -> str | None:
    """Transcribe one image via OpenRouter's vision chat API. None on failure."""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or not image_bytes:
        return None
    try:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": config.vision_api_model(),
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _TRANSCRIBE},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _ENDPOINT,
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
        if resp.status_code != 200:
            log.warning("vision transcribe HTTP %s", resp.status_code)
            return None
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 - extraction must never break an upload
        log.exception("vision transcribe failed")
        return None
    text = (content or "").strip()
    return text or None


async def extract_pdf(path) -> str | None:
    """Text of a PDF: embedded text if present, else vision-transcribe pages."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.warning("PyMuPDF (fitz) not installed; cannot read PDFs")
        return None
    try:
        doc = fitz.open(str(path))
    except Exception:  # noqa: BLE001
        log.exception("could not open PDF %s", path)
        return None
    try:
        pages = [doc.load_page(i).get_text().strip() for i in range(doc.page_count)]
        text = "\n\n".join(p for p in pages if p).strip()
        if len(text) >= _MIN_PDF_TEXT:
            return text
        # Scanned PDF (no embedded text): render pages and transcribe.
        n = min(doc.page_count, _MAX_SCAN_PAGES)
        if doc.page_count > _MAX_SCAN_PAGES:
            log.warning("scanned PDF has %d pages; transcribing first %d",
                        doc.page_count, _MAX_SCAN_PAGES)
        chunks = []
        for i in range(n):
            png = doc.load_page(i).get_pixmap(dpi=200).tobytes("png")
            t = await _vision_transcribe(png, "image/png")
            if t:
                chunks.append(t)
        out = "\n\n".join(chunks).strip()
        return out or None
    except Exception:  # noqa: BLE001
        log.exception("could not extract PDF %s", path)
        return None
    finally:
        doc.close()


async def extract_image(path) -> str | None:
    """Vision-transcribe an image file. None on failure."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        log.exception("could not read image %s", path)
        return None
    ext = os.path.splitext(str(path))[1].lower()
    mime = _MIME_BY_EXT.get(ext, "image/jpeg")
    return await _vision_transcribe(data, mime)
