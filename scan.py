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
2. Find candidate openings two ways:
   a. ALWAYS check these preferred job boards FIRST — WebFetch each and read its
      current listings for relevant roles:
{sources}
   b. ALSO use WebSearch for the user's target roles + preferred locations from
      goals.md. Run a few focused searches.
3. For EACH promising candidate, WebFetch the posting URL and check it:
   - LIVE (hard gate): it must be a CURRENTLY OPEN posting. DROP anything expired,
     closed, filled, dated in the past, or marked "no longer accepting applications"
     / removed. If you cannot fetch the page to confirm it is open, DROP it.
   - EXACT URL (hard gate): record the COMPLETE url you actually fetched and
     confirmed live — copy it VERBATIM, including the full path and any slug. NEVER
     shorten, truncate, strip path segments, drop a trailing slug, or reconstruct a
     url (e.g. do NOT reduce ".../job/342/jobs-fuzzy-sequence-job-senior-full-stack-
     engineer" to ".../job/342/"). The url in your output MUST load the live posting.
   - LOCATION (hard gate): it must be in/near the user's preferred area (KL / Cheras
     / Ampang / Klang Valley) OR fully remote. DROP roles clearly elsewhere and not
     remote.
   - REQUIREMENTS & ARRANGEMENT (score honestly — do NOT hide gaps, do NOT inflate):
     read the stated requirements and compare to the user's REAL background — pivoting
     INTO DevOps/SRE from a strong backend/software base (~5y software, but NOT years
     of dedicated DevOps/SRE/Kubernetes/Terraform production). For a role the user
     only PARTIALLY meets (e.g. "Senior" or "N years" where they have transferable
     backbone but not the exact dedicated years, or an onsite role in a good
     location), KEEP it but give a LOWER fit_score and NAME the gap/tradeoff in
     why_fit (e.g. "wants 4y SRE — you have backend depth, not dedicated SRE years",
     or "onsite in KL, you prefer hybrid"). HARD-DROP only true mismatches: the wrong
     domain, or roles needing core skills the user has essentially none of. Never
     inflate a partial fit to a high score, and never assume experience not in memory.
4. KEEP every opening that is live, in-area (or remote), and on-target for the user's
   goals — score each honestly (clean strong fits high; partial/stretch fits lower
   with the gap named in why_fit). Only drop the truly irrelevant.
5. Do NOT resurface anything the user was already shown. Already shown:
{seen}

Return ONLY a JSON array — no prose, no markdown, no code fences. Each element:
{{"title": "...", "company": "...", "location": "...", "url": "https://...",
  "fit_score": <integer 1-10>, "why_fit": "<=140 chars, concrete",
  "why_aligns": "<=140 chars: which goals/dealbreakers it hits"}}

Only include CURRENTLY-OPEN, in-area (or remote) openings with the EXACT working
application URL you fetched (full path/slug, verbatim), each scored honestly with
any gap named in why_fit. If there are genuinely no relevant new openings, return
exactly: []
"""


def build_prompt(seen_labels: list) -> str:
    seen = "\n".join(f"- {s}" for s in seen_labels) if seen_labels else "  (none yet)"
    sources = ("\n".join(f"      - {u}" for u in config.SCAN_SOURCES)
               if config.SCAN_SOURCES else "      (none configured)")
    return SCAN_PROMPT.format(seen=seen, sources=sources)


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


class ScanError(RuntimeError):
    """A scan failed after a retry (agent error or unparseable output)."""


async def _ask_agent() -> str:
    seen_labels = jobs_store.recent_labels()
    prompt = build_prompt(seen_labels)
    reply, _ = await agent.run_turn(prompt, session_id=None)
    return reply


async def run_scan() -> list:
    """Run one scan. Returns new (unseen) strong matches, already recorded as 'offered'.

    Raises ScanError if the agent fails twice in a row.
    """
    reply = ""  # sentinel; overwritten by _ask_agent below, or ScanError is raised
    try:
        reply = await _ask_agent()
    except Exception:  # noqa: BLE001 - retry once on any backend error
        try:
            reply = await _ask_agent()
        except Exception as e:  # noqa: BLE001
            raise ScanError(str(e)) from e

    seen = jobs_store.seen_ids()
    fresh = []
    for m in parse_matches(reply):
        jid = jobs_store.job_id(m)
        if jid in seen:
            continue
        seen.add(jid)
        m["id"] = jid
        fresh.append(m)

    fresh = fresh[:config.MAX_MATCHES_PER_SCAN]
    for m in fresh:
        jobs_store.record(m, "offered")
    return fresh
