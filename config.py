"""Central configuration and paths for the Career Agent.

The agent runs on the OpenCode CLI pointed at OpenRouter (per-token). Set
OPENROUTER_API_KEY and OPENCODE_MODEL in .env. See docs/opencode-setup.md.
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

# --- opencode backend ------------------------------------------------------
# Path to the OpenCode CLI. Auto-detected if on PATH.
OPENCODE_BIN = os.getenv("OPENCODE_BIN") or shutil.which("opencode") or "opencode"

# Model string in OpenCode's "provider/model" form. Defaults to Gemini 2.5
# Flash-Lite on OpenRouter — cheap, clean output, reliable tool-use and honesty
# (validated live). Swap to another model here. Avoid "thinking" variants, which
# leak chain-of-thought into replies.
OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "openrouter/google/gemini-2.5-flash-lite").strip()

# --- Per-task model overrides ----------------------------------------------
# Each falls back to OPENCODE_MODEL when unset (so leaving them blank keeps
# today's single-model behavior). SCAN_MODEL / RESUME_MODEL drive the dedicated
# scan + Apply-button paths. Setting a distinct CRITIQUE_MODEL or RESUME_MODEL
# switches on the per-message classifier (see classify.py) for TYPED messages.
SCAN_MODEL = os.getenv("SCAN_MODEL", "").strip()
RESUME_MODEL = os.getenv("RESUME_MODEL", "").strip()
CRITIQUE_MODEL = os.getenv("CRITIQUE_MODEL", "").strip()
CLASSIFIER_MODEL = os.getenv("CLASSIFIER_MODEL", "").strip()

_TASK_MODELS = {
    "scan": SCAN_MODEL,
    "resume": RESUME_MODEL,
    "critique": CRITIQUE_MODEL,
    "classifier": CLASSIFIER_MODEL,
}


def model_for(task: str) -> str:
    """Resolve the model slug for a task, falling back to OPENCODE_MODEL.

    task is one of "scan", "resume", "critique", "classifier", or "default".
    """
    return _TASK_MODELS.get(task) or OPENCODE_MODEL


def routing_active() -> bool:
    """True when a distinct critique/resume model is configured.

    Gates the per-message classifier: if the user hasn't set a per-task model
    that differs from the default, the classifier never runs (no added cost).
    """
    return model_for("critique") != OPENCODE_MODEL or model_for("resume") != OPENCODE_MODEL

# Comma-separated Telegram user IDs allowed to use the bot. Empty = allow all
# (you'll be warned). Send /start to learn your ID, then lock it down here.
_allowed = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS = {int(x) for x in _allowed.split(",") if x.strip()} if _allowed else set()

# --- Job discovery ---------------------------------------------------------
# Twice-weekly (default Mon & Thu) + on-demand /scan. The agent web-searches,
# scores strictly against memory, and DMs only strong matches.
JOB_DISCOVERY_ENABLED = os.getenv("JOB_DISCOVERY_ENABLED", "true").strip().lower() != "false"

# Hour (0-23) and timezone the scheduled scan fires at.
SCAN_HOUR = int(os.getenv("SCAN_HOUR", "9"))
SCAN_TZ = os.getenv("SCAN_TZ", "Asia/Kuala_Lumpur").strip()

# Weekdays to scan on, Monday=0 .. Sunday=6. Default Monday + Thursday.
_scan_days = os.getenv("SCAN_WEEKDAYS", "0,3").strip()
SCAN_WEEKDAYS = {int(x) for x in _scan_days.split(",") if x.strip() != ""}

# Cap matches surfaced per scan (keeps Telegram noise down).
MAX_MATCHES_PER_SCAN = int(os.getenv("MAX_MATCHES_PER_SCAN", "5"))

# If true, a scheduled scan that finds nothing stays silent (manual /scan always replies).
SILENT_WHEN_EMPTY = os.getenv("SILENT_WHEN_EMPTY", "true").strip().lower() != "false"

# Chat to DM scheduled matches to. 0 = auto-detect when exactly one user is allowed.
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))

# Preferred job boards the scan checks FIRST (comma-separated URLs), in addition to
# general web search. Add your favourite boards here.
_scan_sources = os.getenv("SCAN_SOURCES", "https://jobs.developerkaki.my/").strip()
SCAN_SOURCES = [s.strip() for s in _scan_sources.split(",") if s.strip()]

# Where seen/decided jobs are persisted (dedup is authoritative here).
JOBS_STORE = BASE_DIR / "data" / "jobs_seen.json"
