"""JobStreet Malaysia listings, straight from the site's public search API.

Why this exists: JobStreet's job pages are behind bot protection. Every
non-browser request gets HTTP 403 — real ids, bogus ids and garbage ids all
return the same 403 body, so the agent's WebFetch cannot read a posting, and
cannot even tell a live one from a dead one. Under the scan's "if you cannot
fetch the page to confirm it is open, DROP it" rule that makes the whole board
invisible.

The search API behind the site's own results page is not protected, and returns
structured records: title, employer, location, salary band, work arrangement,
listing date and a teaser. That is more than WebFetch would have scraped, for
no model tokens and one HTTP call.

Liveness comes from the API itself — it only returns open listings — which is
what lets these bypass the fetch-to-confirm gate.

URL CAVEAT: the API carries no link to the posting, so the detail URL is built
as my.jobstreet.com/job/<id>. That pattern could not be verified from here
(every id, valid or not, answers 403 identically). It renders fine in a
browser; if a link ever 404s for the user, this is the line to fix.
"""
import logging

import httpx

log = logging.getLogger("career-agent.jobstreet")

API = "https://my.jobstreet.com/api/jobsearch/v5/search"
JOB_URL = "https://my.jobstreet.com/job/{job_id}"

# The site's own web client sends these; siteKey picks the country.
SITE_KEY = "MY-Main"
SOURCE_SYSTEM = "houston"

# Cap what one query pulls back. The scan only ever shows the user a handful,
# and each extra row is prompt weight for no gain.
DEFAULT_LIMIT = 20
TIMEOUT = 20.0


def _first_location(raw: dict) -> str:
    for loc in raw.get("locations") or []:
        label = (loc.get("label") or "").strip()
        if label:
            return label
    return ""


def _arrangement(raw: dict) -> str:
    """"On-site" / "Hybrid" / "Remote" — the field the user actually filters on."""
    data = (raw.get("workArrangements") or {}).get("data") or []
    labels = [(a.get("label") or {}).get("text", "").strip() for a in data]
    return ", ".join(x for x in labels if x)


def normalize(raw: dict) -> dict | None:
    """One API record -> the shape the scan prompt reads. None if unusable."""
    if not isinstance(raw, dict):
        return None
    job_id = str(raw.get("id") or "").strip()
    title = (raw.get("title") or "").strip()
    company = (raw.get("companyName")
               or (raw.get("employer") or {}).get("name") or "").strip()
    if not (job_id and title and company):
        return None
    return {
        "title": title,
        "company": company,
        "location": _first_location(raw),
        "url": JOB_URL.format(job_id=job_id),
        "salary": (raw.get("salaryLabel") or "").strip(),
        "arrangement": _arrangement(raw),
        "work_type": ", ".join(raw.get("workTypes") or []),
        "posted": (raw.get("listingDateDisplay") or "").strip(),
        "teaser": (raw.get("teaser") or "").strip(),
    }


async def search(keywords: str, where: str = "Kuala Lumpur",
                 limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Search JobStreet MY. Returns normalized listings, or [] on any failure.

    Never raises: a board being down must degrade the scan, not abort it —
    the agent still has WebSearch and the other configured sources.
    """
    params = {
        "siteKey": SITE_KEY,
        "sourcesystem": SOURCE_SYSTEM,
        "keywords": keywords,
        "where": where,
        "page": 1,
        "pageSize": max(1, min(limit, 100)),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(API, params=params,
                                 headers={"Accept": "application/json"})
            r.raise_for_status()
            payload = r.json()
    except Exception as e:  # noqa: BLE001 — any failure means "no results"
        log.warning("JobStreet search for %r failed: %s: %s",
                    keywords, type(e).__name__, e)
        return []

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        log.warning("JobStreet returned no 'data' array for %r.", keywords)
        return []

    out = []
    for raw in rows[:limit]:
        item = normalize(raw)
        if item:
            out.append(item)
    log.info("JobStreet: %d listings for %r in %r (of %s total).",
             len(out), keywords, where, payload.get("totalCount"))
    return out


async def search_many(queries: list[str], where: str = "Kuala Lumpur",
                      limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Run several keyword queries and merge, keeping first-seen order.

    Queries run one after another rather than in parallel: the scan is not
    latency-bound, and hammering a public API with a burst is how a scraper
    earns itself a block.
    """
    seen = set()
    merged = []
    for q in queries:
        for item in await search(q, where=where, limit=limit):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            merged.append(item)
    return merged


def as_prompt_block(listings: list[dict], cap: int = 25) -> str:
    """Render listings for the scan prompt. Empty string if there are none."""
    if not listings:
        return ""
    if len(listings) > cap:
        log.info("JobStreet: showing the agent %d of %d listings (cap).",
                 cap, len(listings))
    lines = []
    for j in listings[:cap]:
        bits = [f'- {j["title"]} — {j["company"]}']
        detail = " | ".join(x for x in (j["location"], j["arrangement"],
                                        j["work_type"], j["salary"],
                                        j["posted"]) if x)
        if detail:
            bits.append(f"  {detail}")
        if j["teaser"]:
            bits.append(f'  {j["teaser"][:200]}')
        bits.append(f'  {j["url"]}')
        lines.append("\n".join(bits))
    return "\n".join(lines)
