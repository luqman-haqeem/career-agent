"""Extract text from uploaded PDFs and images, server-side.

OpenCode's --file forwarding for binary uploads (PDF, images) does not reliably
reach the model, so we extract text ourselves and inline it into the prompt —
the same approach bot._docx_to_text uses. Text-based PDFs are read with PyMuPDF;
images and scanned (no-text) PDFs are transcribed by a vision model via a direct
OpenRouter call.

Every function degrades to None on any failure (missing library, missing API
key, API error, unreadable file) so an upload never crashes the bot.
"""
import asyncio
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


def _content_to_text(content) -> str:
    """Normalize an OpenRouter message content to text.

    Content is usually a string, but some providers return a list of parts
    (e.g. [{"type": "text", "text": "..."}]). Concatenate the text of those.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts)
    return ""


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
    text = _content_to_text(content).strip()
    return text or None


def _read_pdf_pages(path):
    """Synchronous PyMuPDF work: embedded text per page + rendered scans.

    Returns (page_texts, renders, dropped) where renders is a list of
    (page_index, png_bytes) for the no-text pages within the vision budget, or
    None if PyMuPDF is missing or the PDF can't be read. Runs in a worker thread
    (see extract_pdf) so it never blocks the event loop.
    """
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
        page_texts = [doc.load_page(i).get_text().strip()
                      for i in range(doc.page_count)]
        # Render only the pages that lack real embedded text (scanned), capped.
        budget = _MAX_SCAN_PAGES
        dropped = 0
        renders = []
        for i, t in enumerate(page_texts):
            if len(t) >= _MIN_PDF_TEXT:
                continue
            if budget <= 0:
                dropped += 1
                continue
            budget -= 1
            renders.append((i, doc.load_page(i).get_pixmap(dpi=200).tobytes("png")))
        return page_texts, renders, dropped
    except Exception:  # noqa: BLE001
        log.exception("could not extract PDF %s", path)
        return None
    finally:
        doc.close()


async def extract_pdf(path) -> str | None:
    """Text of a PDF: embedded text if present, else vision-transcribe pages."""
    result = await asyncio.to_thread(_read_pdf_pages, path)
    if result is None:
        return None
    page_texts, renders, dropped = result
    # Vision-transcribe the scanned pages (async; off-thread render already done).
    transcribed = {}
    for i, png in renders:
        text = await _vision_transcribe(png, "image/png")
        if text:
            transcribed[i] = text
    # Reassemble in page order: embedded text where present, else its transcript.
    chunks = []
    for i, t in enumerate(page_texts):
        if len(t) >= _MIN_PDF_TEXT:
            chunks.append(t)
        elif i in transcribed:
            chunks.append(transcribed[i])
    if dropped:
        log.warning("scanned PDF: dropped %d page(s) beyond vision budget", dropped)
    return "\n\n".join(c for c in chunks if c).strip() or None


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
