"""Per-job conversation threads for one Telegram chat.

The bot used to keep ONE OpenCode session per chat, so every job link, every
"make that bullet shorter", and every memory update shared a single history.
Working two jobs at once meant the model had to guess which resume "fix it"
referred to, and a poisoned turn took the whole conversation down with it.

A thread is just a named conversation: its own OpenCode session, its own
resume. Messages are bound to a thread by Telegram message id, so replying to
a message routes the turn back into the thread it came from.

Pure and testable: imports no Telegram or agent code, and reads paths from
`config` inside each function so tests can monkeypatch config.BASE_DIR.
"""
import datetime as _dt
import json
import re
import secrets
from urllib.parse import urlparse

import config

# The default thread — general chat, memory updates, onboarding. Its session
# file keeps the pre-thread path (`<chat_id>.json`), so an existing install
# keeps its conversation across this upgrade with no migration step.
MAIN = "main"

# Telegram message ids we remember per chat. Every bot message gets bound, so
# without a cap this grows for the life of the install. 500 covers far more
# scrollback than anyone replies into.
_MAX_MESSAGE_BINDINGS = 500

_URL_RE = re.compile(r"https?://\S+")


def _path(chat_id: int):
    return config.BASE_DIR / "data" / "threads" / f"{chat_id}.json"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _load(chat_id: int) -> dict:
    try:
        data = json.loads(_path(chat_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {"threads": {}, "messages": {}}
    if not isinstance(data, dict):
        return {"threads": {}, "messages": {}}
    data.setdefault("threads", {})
    data.setdefault("messages", {})
    return data


def _save(chat_id: int, data: dict) -> None:
    p = _path(chat_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def find_url(text: str):
    """First http(s) URL in text, or None."""
    m = _URL_RE.search(text or "")
    return m.group(0) if m else None


def label_for_url(url: str) -> str:
    """A short human label for a thread started from a link.

    Just the host — the company isn't known until the agent has read the JD.
    `set_resume` upgrades the label to the resume slug once one is written.
    """
    host = (urlparse(url).netloc or url).lower()
    return host[4:] if host.startswith("www.") else host


def new_thread(chat_id: int, label: str) -> str:
    """Create a thread and return its key."""
    data = _load(chat_id)
    key = f"t{secrets.token_urlsafe(4)}"
    while key in data["threads"]:  # collision is vanishingly rare, but cheap to rule out
        key = f"t{secrets.token_urlsafe(4)}"
    now = _now_iso()
    data["threads"][key] = {"label": label, "created_at": now, "last_at": now,
                            "resume": None}
    _save(chat_id, data)
    return key


def get(chat_id: int, key: str):
    """Thread metadata, or None if the key is unknown."""
    return _load(chat_id)["threads"].get(key)


def exists(chat_id: int, key: str) -> bool:
    return key == MAIN or key in _load(chat_id)["threads"]


def bind_message(chat_id: int, message_id: int, key: str) -> None:
    """Record that `message_id` belongs to thread `key`, so a reply to it routes back."""
    if not message_id:
        return
    data = _load(chat_id)
    data["messages"][str(message_id)] = key
    if len(data["messages"]) > _MAX_MESSAGE_BINDINGS:
        # Message ids climb monotonically within a chat, so the numerically
        # smallest are the oldest.
        for stale in sorted(data["messages"], key=int)[:-_MAX_MESSAGE_BINDINGS]:
            del data["messages"][stale]
    _save(chat_id, data)


def thread_for_message(chat_id: int, message_id):
    """Which thread a message belongs to, or None if it isn't bound."""
    if not message_id:
        return None
    return _load(chat_id)["messages"].get(str(message_id))


def touch(chat_id: int, key: str) -> None:
    """Mark a thread as just used (drives /threads ordering)."""
    data = _load(chat_id)
    if key in data["threads"]:
        data["threads"][key]["last_at"] = _now_iso()
        _save(chat_id, data)


def set_resume(chat_id: int, key: str, filename: str) -> None:
    """Record the resume a thread owns, and name the thread after it.

    The label starts as a bare hostname because the company is unknown when a
    link arrives; the resume slug is the first point at which we have a name
    worth showing.
    """
    data = _load(chat_id)
    t = data["threads"].get(key)
    if not t:
        return
    t["resume"] = filename
    stem = filename.rsplit(".", 1)[0]
    if stem:
        t["label"] = stem
    _save(chat_id, data)


def resume_owner(chat_id: int, filename: str):
    """Which thread owns a resume file, or None.

    Delivery uses this to keep one thread's PDF out of another thread's chat.
    """
    for key, t in _load(chat_id)["threads"].items():
        if t.get("resume") == filename:
            return key
    return None


def listing(chat_id: int) -> list:
    """(key, meta) for every thread, most recently used first."""
    data = _load(chat_id)
    return sorted(data["threads"].items(),
                  key=lambda kv: kv[1].get("last_at") or "", reverse=True)


def forget(chat_id: int, key: str) -> None:
    """Drop a thread and every message bound to it."""
    data = _load(chat_id)
    data["threads"].pop(key, None)
    data["messages"] = {m: k for m, k in data["messages"].items() if k != key}
    _save(chat_id, data)


def forget_all(chat_id: int) -> None:
    _save(chat_id, {"threads": {}, "messages": {}})
