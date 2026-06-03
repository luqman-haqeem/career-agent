"""Agent brain: drives a CLI agent harness in headless mode.

Two interchangeable backends, selected by config.AI_BACKEND:

- "claude_cli" (DEFAULT): the local Claude Code CLI (`claude`), on your
  subscription — no API key, full native tools (Read/Edit/Write/WebFetch/...).
- "opencode": the OpenCode CLI (`opencode`), pointed at whatever model/provider
  you've configured. See docs/opencode-setup.md.

Both expose the same contract:
    run_turn(user_message, session_id=None, files=None) -> (reply_text, session_id)
so the Telegram front-end never needs to know which backend is active.
"""
import asyncio
import json
import os

import config

# Tools the career agent needs on the Claude path — no Bash/shell access.
ALLOWED_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite"]


def _strip_api_key_env() -> dict:
    """Force subscription auth by ensuring no API key leaks into the CLI."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


# --- claude_cli backend ----------------------------------------------------
def _claude_build_args(user_message: str, session_id: str | None) -> list:
    args = [
        config.CLAUDE_BIN,
        "-p", user_message,
        "--output-format", "json",
        "--model", config.MODEL,
        "--permission-mode", "acceptEdits",
        "--add-dir", str(config.BASE_DIR),
        "--max-turns", "30",
    ]
    if session_id:
        args += ["--resume", session_id]
    # Variadic flag last so it doesn't swallow other options.
    args += ["--allowedTools", *ALLOWED_TOOLS]
    return args


async def _claude_invoke(user_message: str, session_id: str | None):
    proc = await asyncio.create_subprocess_exec(
        *_claude_build_args(user_message, session_id),
        cwd=str(config.BASE_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_strip_api_key_env(),
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def _claude_cli_run_turn(user_message: str, session_id: str | None = None, files=None):
    """Run one turn via the Claude Code CLI. Returns (reply_text, session_id).

    The Claude path reads uploads natively via the path embedded in the prompt,
    so `files` is unused here. Falls back to a fresh session if resume fails.
    """
    rc, out, err = await _claude_invoke(user_message, session_id)

    # If resuming failed, retry once as a brand-new conversation.
    if rc != 0 and session_id:
        rc, out, err = await _claude_invoke(user_message, None)

    if rc != 0:
        raise RuntimeError(err.strip() or out.strip() or f"claude exited with code {rc}")

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return out.strip() or "(no response)", session_id

    text = data.get("result") or "(no response)"
    new_session = data.get("session_id") or session_id
    if data.get("is_error"):
        text = f"⚠️ {text}"
    return text, new_session


# --- opencode backend ------------------------------------------------------
def _opencode_build_args(user_message: str, session_id: str | None, files) -> list:
    args = [
        config.OPENCODE_BIN, "run", user_message,
        "--format", "json",
        "--dir", str(config.BASE_DIR),
        "--dangerously-skip-permissions",
    ]
    if config.OPENCODE_MODEL:
        args += ["--model", config.OPENCODE_MODEL]
    if session_id:
        args += ["--session", session_id]
    for f in files or []:
        args += ["--file", str(f)]
    return args


async def _opencode_invoke(user_message: str, session_id: str | None, files):
    proc = await asyncio.create_subprocess_exec(
        *_opencode_build_args(user_message, session_id, files),
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


async def _opencode_run_turn(user_message: str, session_id: str | None = None, files=None):
    """Run one turn via the OpenCode CLI. Returns (reply_text, session_id)."""
    rc, out, err = await _opencode_invoke(user_message, session_id, files)

    # If resuming a stale/expired session failed, retry once fresh.
    if rc != 0 and session_id:
        rc, out, err = await _opencode_invoke(user_message, None, files)

    if rc != 0:
        raise RuntimeError(err.strip() or out.strip() or f"opencode exited with code {rc}")

    reply, new_session = _opencode_parse(out, session_id)
    return reply or "(no response)", new_session


# --- dispatcher ------------------------------------------------------------
async def run_turn(user_message: str, session_id: str | None = None, files=None):
    """Run one user turn on the configured backend. Returns (reply, session_id)."""
    if config.AI_BACKEND == "opencode":
        return await _opencode_run_turn(user_message, session_id, files)
    return await _claude_cli_run_turn(user_message, session_id, files)
