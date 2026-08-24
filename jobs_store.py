"""Persisted dedup + decision store for discovered jobs.

Single JSON file at config.JOBS_STORE: {"jobs": {job_id: {...}}}. The job_id is a
stable hash so the same opening is never surfaced twice, even across scans. State
is one of: "offered" (shown), "applied" (user tapped Apply), "skipped".
"""
import datetime as _dt
import hashlib
import json

import config


def _path():
    return config.JOBS_STORE


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {"jobs": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"jobs": {}}
    if not isinstance(data, dict) or "jobs" not in data:
        return {"jobs": {}}
    return data


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def job_id(job: dict) -> str:
    """Stable 16-hex id. Prefers the (normalized) URL, falls back to company|title."""
    url = (job.get("url") or "").strip().lower().rstrip("/")
    if url:
        key = url
    else:
        key = f"{(job.get('company') or '').strip().lower()}|{(job.get('title') or '').strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def seen_ids() -> set:
    return set(_load()["jobs"].keys())


def get(jid: str):
    return _load()["jobs"].get(jid)


def record(job: dict, state: str) -> str:
    jid = job_id(job)
    data = _load()
    entry = data["jobs"].get(jid, {})
    entry.update({
        "id": jid,
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "url": job.get("url", ""),
        "fit_score": job.get("fit_score"),
    })
    incoming_reasons = [str(s).strip() for s in (job.get("skip_reasons") or [])
                        if str(s).strip()]
    if incoming_reasons:
        entry["skip_reasons"] = incoming_reasons
    else:
        entry.setdefault("skip_reasons", [])
    entry.setdefault("first_seen", _now_iso())
    entry.setdefault("state", state)
    data["jobs"][jid] = entry
    _save(data)
    return jid


def set_decision(jid: str, state: str, reason: str = None) -> None:
    """Record a user decision: state (+ optional skip reason) and a timestamp.

    Silent no-op if jid is unknown.
    """
    data = _load()
    entry = data["jobs"].get(jid)
    if not entry:
        return
    entry["state"] = state
    if reason:
        entry["reason"] = reason
    else:
        entry.pop("reason", None)
    entry["decided_at"] = _now_iso()
    _save(data)


def set_draft(jid: str, filename: str, thread: str, note: str) -> None:
    """Attach a pre-built resume to a job. Deliberately NOT a decision.

    decisions() feeds preferences.run_synthesis(), which rewrites
    memory/preferences.md, which re-ranks the next scan. A resume drafted on
    the user's behalf is not a choice they made, so this leaves "state" alone —
    otherwise the scanner would end up learning from its own output.

    Silent no-op if jid is unknown.
    """
    data = _load()
    entry = data["jobs"].get(jid)
    if not entry:
        return
    entry["resume_file"] = filename
    entry["resume_thread"] = thread
    entry["resume_note"] = note
    entry["drafted_at"] = _now_iso()
    _save(data)


def clear_draft(jid: str) -> None:
    """Drop a stored draft — its file went missing, so stop advertising it."""
    data = _load()
    entry = data["jobs"].get(jid)
    if not entry:
        return
    for k in ("resume_file", "resume_thread", "resume_note", "drafted_at"):
        entry.pop(k, None)
    _save(data)


def set_state(jid: str, state: str) -> None:
    """Back-compat shim: set state with no reason."""
    set_decision(jid, state)


def decisions(limit: int = 40) -> list:
    """Recently DECIDED jobs (applied/skipped), newest first — synthesis input."""
    jobs = [j for j in _load()["jobs"].values()
            if j.get("state") in ("applied", "skipped")]
    jobs.sort(key=lambda j: j.get("decided_at") or j.get("first_seen") or "",
              reverse=True)
    out = []
    for j in jobs[:limit]:
        out.append({
            "id": j.get("id", ""),
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "fit_score": j.get("fit_score"),
            "state": j.get("state", ""),
            "reason": j.get("reason", ""),
            "decided_at": j.get("decided_at", ""),
        })
    return out


def last_synthesis_at() -> str | None:
    """ISO timestamp of the last preference synthesis, or None."""
    return _load().get("last_synthesis_at")


def mark_synthesis() -> None:
    """Stamp the current time as the last synthesis run."""
    data = _load()
    data["last_synthesis_at"] = _now_iso()
    _save(data)


def recent_labels(limit: int = 40) -> list:
    """Human-readable 'Title @ Company' for recently seen jobs (newest first).

    Used only to nudge the agent to skip repeats; real dedup is by job_id.
    """
    jobs = list(_load()["jobs"].values())
    jobs.sort(key=lambda j: j.get("first_seen", ""), reverse=True)
    out = []
    for j in jobs[:limit]:
        title, company = j.get("title", ""), j.get("company", "")
        if title or company:
            out.append(f"{title} @ {company}")
    return out
