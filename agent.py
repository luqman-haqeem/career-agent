"""Agent brain: drives the OpenCode CLI in headless mode.

Single backend: OpenCode (`opencode`), pointed at whatever model/provider
you've configured. See docs/opencode-setup.md.

Contract:
    run_turn(user_message, session_id=None, files=None, model=None) -> (reply_text, session_id)
"""
import asyncio
import json
import os

import config

# --- opencode backend ------------------------------------------------------
def _opencode_build_args(user_message: str, session_id: str | None, files, model=None) -> list:
    args = [
        config.OPENCODE_BIN, "run", user_message,
        "--format", "json",
        "--dir", str(config.BASE_DIR),
        "--dangerously-skip-permissions",
    ]
    chosen = model or config.OPENCODE_MODEL
    if chosen:
        args += ["--model", chosen]
    if session_id:
        args += ["--session", session_id]
    for f in files or []:
        args += ["--file", str(f)]
    return args


async def _opencode_invoke(user_message: str, session_id: str | None, files, model=None):
    proc = await asyncio.create_subprocess_exec(
        *_opencode_build_args(user_message, session_id, files, model),
        cwd=str(config.BASE_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(os.environ),  # OpenCode uses its own configured provider auth.
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _opencode_parse(out: str, fallback_session: str | None):
    """Parse OpenCode's JSONL event stream into (reply_text, session_id).

    OpenCode emits one JSON object per line. Assistant text lives in message
    "parts" of type "text"; we key by part id and keep the latest snapshot per
    part (parts carry their full current text, so the last update wins), then
    join in first-seen order. Session id is the `sessionID` field on events
    (format `ses_...`). NOTE: validated against OpenCode's documented event
    shape; confirm with a live run (see docs/opencode-setup.md) as the stream
    schema can evolve.
    """
    session_id = fallback_session
    order = []
    texts = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        sid = ev.get("sessionID") or part.get("sessionID")
        if sid:
            session_id = sid
        if part.get("type") == "text":
            pid = part.get("id") or f"_{len(order)}"
            if pid not in texts:
                order.append(pid)
            txt = part.get("text")
            if txt is not None:
                texts[pid] = txt
    reply = "".join(texts[p] for p in order).strip()
    return reply, session_id


async def _opencode_run_turn(user_message: str, session_id: str | None = None, files=None, model=None):
    """Run one turn via the OpenCode CLI. Returns (reply_text, session_id)."""
    rc, out, err = await _opencode_invoke(user_message, session_id, files, model)

    # If resuming a stale/expired session failed, retry once fresh.
    if rc != 0 and session_id:
        rc, out, err = await _opencode_invoke(user_message, None, files, model)

    if rc != 0:
        raise RuntimeError(err.strip() or out.strip() or f"opencode exited with code {rc}")

    reply, new_session = _opencode_parse(out, session_id)
    return reply or "(no response)", new_session


# --- dispatcher ------------------------------------------------------------
async def run_turn(user_message: str, session_id: str | None = None, files=None, model=None):
    """Run one user turn via OpenCode. Returns (reply_text, session_id).

    `model` overrides config.OPENCODE_MODEL for this turn (None = default).
    """
    return await _opencode_run_turn(user_message, session_id, files, model)
