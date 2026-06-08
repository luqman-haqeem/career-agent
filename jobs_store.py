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
    return _dt.datetime.now().isoformat(timespec="seconds")


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
    jid = job.get("id") or job_id(job)
    data = _load()
    entry = data["jobs"].get(jid, {})
    entry.update({
        "id": jid,
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "url": job.get("url", ""),
        "fit_score": job.get("fit_score"),
        "state": state,
    })
    entry.setdefault("first_seen", _now_iso())
    data["jobs"][jid] = entry
    _save(data)
    return jid


def set_state(jid: str, state: str) -> None:
    data = _load()
    if jid in data["jobs"]:
        data["jobs"][jid]["state"] = state
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
