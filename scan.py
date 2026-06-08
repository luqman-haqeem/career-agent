"""One job-discovery scan: prompt the agent, parse its JSON, filter against the store.

The agent runs in an EPHEMERAL session (session_id=None) so scans never bloat the
user's chat conversation. Dedup is authoritative via jobs_store; the seen-labels in
the prompt are only a hint to the agent.
"""
import json

import agent
import config
import jobs_store

SCAN_PROMPT = """You are running an automated JOB DISCOVERY scan for the user.

Do this:
1. Read memory/profile.md and memory/goals.md with your Read tool.
2. Use WebSearch to find CURRENT job openings matching the user's target roles and
   preferred locations from goals.md. Run a few focused searches.
3. Judge each opening STRICTLY against the user's profile and goals:
   - Keep ONLY strong matches: clear relevance to a target role AND it must pass the
     user's dealbreakers (work arrangement + location) in goals.md.
   - Drop weak or partial fits. When in doubt, drop it.
4. Do NOT resurface anything the user was already shown. Already shown:
{seen}

Return ONLY a JSON array — no prose, no markdown, no code fences. Each element:
{{"title": "...", "company": "...", "location": "...", "url": "https://...",
  "fit_score": <integer 1-10>, "why_fit": "<=140 chars, concrete",
  "why_aligns": "<=140 chars: which goals/dealbreakers it hits"}}

Only include openings with a real, working application URL. If there are no strong
new matches, return exactly: []
"""


def build_prompt(seen_labels: list) -> str:
    seen = "\n".join(f"- {s}" for s in seen_labels) if seen_labels else "  (none yet)"
    return SCAN_PROMPT.format(seen=seen)


def _extract_array(text: str):
    """Pull the first JSON array out of the agent's reply, tolerating fences/prose.

    Tries a clean whole-string parse first; otherwise scans successive '[' positions
    (paired with the last ']') so a bracketed hedge in prose before the real array
    doesn't cause the whole result to be dropped. Returns [] if nothing parses.
    """
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    end = text.rfind("]")
    if end == -1:
        return []
    idx = 0
    while True:
        start = text.find("[", idx)
        if start == -1 or end < start:
            return []
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
        idx = start + 1


def valid_match(m: dict) -> bool:
    return bool(
        isinstance(m, dict)
        and (m.get("title") or "").strip()
        and (m.get("company") or "").strip()
        and (m.get("url") or "").strip()
    )


def _coerce(m: dict) -> dict:
    """Normalize a match dict in place (string fit_score -> int, or None)."""
    score = m.get("fit_score")
    if isinstance(score, str):
        try:
            m["fit_score"] = int(score.strip())
        except ValueError:
            m["fit_score"] = None
    return m


def parse_matches(text: str) -> list:
    return [_coerce(m) for m in _extract_array(text) if valid_match(m)]
