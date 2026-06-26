"""Classify a typed chat message into a task label, to pick its model.

A cheap, direct OpenRouter chat-completion — NOT an opencode agent (which would
load CLAUDE.md + every tool just to emit one word). Any failure falls back to
"default" so a chat turn is never broken or stalled.
"""
import os

import httpx

import config

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT = 6.0

_SYSTEM = (
    "You label a user's message to a career-assistant bot. Reply with EXACTLY "
    "one lowercase word, no punctuation: 'critique' if they want an existing "
    "resume scored, rated, reviewed, or critiqued; 'resume' if they want a "
    "resume written, built, tailored, or updated; otherwise 'default'."
)


def _api_model() -> str:
    """OpenRouter's API wants 'provider/model'; strip a leading 'openrouter/'."""
    m = config.model_for("classifier")
    prefix = "openrouter/"
    return m[len(prefix):] if m.startswith(prefix) else m


async def classify_task(text: str) -> str:
    """Return 'critique', 'resume', or 'default'. Never raises."""
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or not (text or "").strip():
        return "default"
    try:
        payload = {
            "model": _api_model(),
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": text[:2000]},
            ],
            "max_tokens": 5,
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _ENDPOINT,
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
        if resp.status_code != 200:
            return "default"
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 - classification must never break a chat
        return "default"
    label = (content or "").strip().lower()
    for cand in ("critique", "resume"):
        if cand in label:
            return cand
    return "default"
