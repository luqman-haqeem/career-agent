"""Distill the user's Apply/Skip history into memory/preferences.md.

Runs an EPHEMERAL agent turn (session_id=None) so it never bloats the user's chat.
The file holds INFERRED preferences (not facts); it influences job ranking and fit
advice only, never resume content. See CLAUDE.md.
"""
import json

import agent
import jobs_store

SYNTH_PROMPT = """You are updating the user's INFERRED job preferences from their
recent Apply/Skip decisions on discovered jobs.

Recent decisions (newest first), as JSON:
{decisions}

Do this:
1. Read memory/preferences.md with your Read tool. It may not exist yet — if so,
   create it with a one-line header noting it holds INFERRED preferences (not facts)
   used only to rank jobs and shape fit advice.
2. Look for consistent patterns in what the user APPLIES to vs SKIPS, and WHY they
   skip (the "reason" field, including free-text) — e.g. repeatedly skipping onsite
   roles, a seniority band, or a tech/domain.
3. Update memory/preferences.md: keep the header, then add/merge/remove SHORT bullets
   (a handful, no more) so it reflects the current patterns. These are INFERENCES, not
   facts — phrase them as tendencies and cite rough evidence, e.g.
   "Skips onsite-only roles (3/3 onsite skipped)". RESPECT any bullet the user clearly
   wrote themselves; never delete user-authored lines.
4. Never invent decisions not present above. If there isn't enough signal to change
   anything, leave the file as-is.

Then reply with EXACTLY ONE of:
- a single line (<=140 chars) summarizing what you changed, phrased for the user,
  e.g. "Noticed you keep skipping onsite roles — I'll weight those lower."
- the literal token NO_CHANGE   (if you made no meaningful change)

Output nothing else.
"""


def needs_synthesis() -> bool:
    """True if there are decisions newer than the last synthesis (or never run)."""
    decs = jobs_store.decisions()
    if not decs:
        return False
    last = jobs_store.last_synthesis_at()
    if not last:
        return True
    newest = max((d.get("decided_at") or "") for d in decs)
    return newest > last


async def run_synthesis():
    """Update preferences.md from recent decisions. Returns a one-line summary for
    the user, or None (nothing to do / no change / agent error)."""
    if not needs_synthesis():
        return None
    prompt = SYNTH_PROMPT.format(decisions=json.dumps(jobs_store.decisions(), indent=2))
    try:
        reply, _ = await agent.run_turn(prompt, session_id=None)
    except Exception:  # noqa: BLE001 - synthesis must never break the scan
        return None
    jobs_store.mark_synthesis()
    lines = [ln.strip() for ln in (reply or "").strip().splitlines() if ln.strip()]
    summary = lines[0] if lines else ""
    if not summary or summary.upper() == "NO_CHANGE":
        return None
    return summary[:140]
