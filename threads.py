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


def _blank() -> dict:
    return {"threads": {}, "messages": {}, "current": None}


def _load(chat_id: int) -> dict:
    try:
        data = json.loads(_path(chat_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return _blank()
    if not isinstance(data, dict):
        return _blank()
    data.setdefault("threads", {})
    data.setdefault("messages", {})
    data.setdefault("current", None)
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


# Real postings run long once they list a stack ("Full Stack Developer
# (NodeJS/ReactJS/AWS) — Net2Source (N2S)" is 60 on its own), so leave room to
# spare. A header that wraps to two lines is fine; one that loses the employer
# is not.
_MAX_LABEL = 80
# Below this, a trimmed position is too stubby to identify anything, so drop it
# rather than show "Sen… — Company".
_MIN_POSITION = 8


def format_label(position: str, company: str) -> str:
    """Render a thread label as 'Position — Company', dropping either if absent.

    When the pair is too long, the POSITION is trimmed and the company kept
    whole — a plain right-hand cut would chop the employer, which is the half
    that actually identifies the job.
    """
    position = (position or "").strip()
    company = (company or "").strip()
    if not company:
        return position[:_MAX_LABEL]
    if not position:
        return company[:_MAX_LABEL]
    full = f"{position} — {company}"
    if len(full) <= _MAX_LABEL:
        return full
    room = _MAX_LABEL - len(company) - len(" — ") - len("…")
    if room < _MIN_POSITION:
        return company[:_MAX_LABEL]
    return f"{position[:room].rstrip()}… — {company}"


def new_thread(chat_id: int, label: str, named: bool = False,
               url: str = None) -> str:
    """Create a thread, make it current, and return its key.

    `named` marks a label as real (a position and company we actually know)
    rather than provisional. A provisional label — the hostname we fall back to
    when a link arrives before anyone has read the JD — gets replaced as soon as
    something better turns up; a real one is never silently overwritten.

    `url` is the posting this thread came from. Kept so a thread whose OpenCode
    session is lost can be re-primed from the source instead of asking the user
    to resend a link they already sent.
    """
    data = _load(chat_id)
    key = f"t{secrets.token_urlsafe(4)}"
    while key in data["threads"]:  # collision is vanishingly rare, but cheap to rule out
        key = f"t{secrets.token_urlsafe(4)}"
    now = _now_iso()
    data["threads"][key] = {"label": label[:_MAX_LABEL], "named": bool(named),
                            "url": url, "created_at": now, "last_at": now,
                            "resume": None}
    data["current"] = key
    _save(chat_id, data)
    return key


def set_label(chat_id: int, key: str, label: str) -> None:
    """Give a thread its real 'Position — Company' name."""
    data = _load(chat_id)
    t = data["threads"].get(key)
    if not t or not (label or "").strip():
        return
    t["label"] = label.strip()[:_MAX_LABEL]
    t["named"] = True
    _save(chat_id, data)


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
    """Record the resume a thread owns.

    A still-provisional thread (labelled with the bare hostname a link came
    from) borrows the resume slug as a name, since that beats "linkedin.com".
    A thread already carrying a real Position — Company keeps it: the slug is
    a filename, not a title.
    """
    data = _load(chat_id)
    t = data["threads"].get(key)
    if not t:
        return
    t["resume"] = filename
    stem = filename.rsplit(".", 1)[0]
    if stem and not t.get("named"):
        t["label"] = stem[:_MAX_LABEL]
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
    if data.get("current") == key:
        data["current"] = None   # never leave typing pointed at a dead thread
    _save(chat_id, data)


def forget_all(chat_id: int) -> None:
    _save(chat_id, _blank())


# --- the "current" thread --------------------------------------------------
# Requiring a reply to continue a job reads fine on paper and fails in
# practice: when the bot ends a turn with a question ("Want me to judge fit?"),
# nobody swipes-to-reply to answer "yes" -- they type it, and it lands in the
# main conversation with none of the job's context. So the last thread you used
# stays current, and typing continues it. Replying still switches threads, and
# every reply carries its thread's label, so you can always see where you are.
def set_current(chat_id: int, key) -> None:
    data = _load(chat_id)
    if key is not None and key != MAIN and key not in data["threads"]:
        return
    data["current"] = None if key == MAIN else key
    _save(chat_id, data)


def current(chat_id: int) -> str:
    """The thread typing continues, or MAIN."""
    data = _load(chat_id)
    key = data.get("current")
    return key if key and key in data["threads"] else MAIN
