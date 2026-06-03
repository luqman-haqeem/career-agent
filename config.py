"""Central configuration and paths for the Career Agent.

This build runs on your local Claude Code subscription (the `claude` CLI),
so there is NO Anthropic API key. Usage counts against your subscription.
"""
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- Directories -----------------------------------------------------------
MEMORY_DIR = BASE_DIR / "memory"
EXPERIENCES_DIR = MEMORY_DIR / "experiences"
PROJECTS_DIR = MEMORY_DIR / "projects"
RESUMES_DIR = BASE_DIR / "resumes"
UPLOADS_DIR = BASE_DIR / "uploads"
SESSIONS_DIR = BASE_DIR / "data" / "sessions"

for _d in (MEMORY_DIR, EXPERIENCES_DIR, PROJECTS_DIR, RESUMES_DIR, UPLOADS_DIR, SESSIONS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Settings --------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# --- AI backend (which "brain" answers) ------------------------------------
# "claude_cli" (DEFAULT): runs on your Claude Code subscription via the local
#   `claude` CLI — no API key, no per-token cost, and full native tools
#   (file read/write, PDF/image reading, web fetch, session resume) for free.
# "opencode": runs the open-source OpenCode CLI agent (https://opencode.ai),
#   pointed at any model/provider you've configured (OpenRouter, Anthropic API,
#   a local model, or your subscription via a community plugin). Provided as an
#   OPTION; switch by setting AI_BACKEND=opencode in .env. See docs/opencode-setup.md.
AI_BACKEND = os.getenv("AI_BACKEND", "claude_cli").strip().lower()

# --- claude_cli backend ----------------------------------------------------
# Path to the Claude Code CLI. Auto-detected if on PATH.
CLAUDE_BIN = os.getenv("CLAUDE_BIN") or shutil.which("claude") or "claude"

# Model the agent uses. Opus is highest quality; switch to a Sonnet id to use
# fewer subscription credits, e.g. CAREER_AGENT_MODEL=claude-sonnet-4-5
MODEL = os.getenv("CAREER_AGENT_MODEL", "claude-opus-4-8")

# --- opencode backend (only used when AI_BACKEND=opencode) -----------------
# Path to the OpenCode CLI. Auto-detected if on PATH.
OPENCODE_BIN = os.getenv("OPENCODE_BIN") or shutil.which("opencode") or "opencode"

# Model string in OpenCode's "provider/model" form, e.g.
# "anthropic/claude-sonnet-4.5" or "openrouter/anthropic/claude-sonnet-4.5".
# If blank, OpenCode uses whatever model it's configured to default to.
OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "").strip()

# Comma-separated Telegram user IDs allowed to use the bot. Empty = allow all
# (you'll be warned). Send /start to learn your ID, then lock it down here.
_allowed = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS = {int(x) for x in _allowed.split(",") if x.strip()} if _allowed else set()
