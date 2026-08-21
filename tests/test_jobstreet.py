"""The JobStreet provider.

The board's job pages answer 403 to every non-browser request — real ids,
bogus ids and garbage ids all return the same body — so the agent can neither
read a posting nor tell a live one from a dead one. These listings come from
the site's own search API instead, verified live on 2026-08-21 (378 hits for
"python developer" in Kuala Lumpur).
"""
import json

import httpx
import pytest

import config
import jobstreet
import scan

# Trimmed from a real API response; field names and nesting are verbatim.
RAW = {
    "id": "94012364",
    "title": "Full-Stack Developer (Node.js, JavaScript/TypeScript, Python)",
    "companyName": "Hexa Business",
    "employer": {"name": "Hexa Business"},
    "locations": [{"label": "Kuala Lumpur City Centre, Kuala Lumpur",
                   "countryCode": "MY"}],
    "salaryLabel": "RM 5,000 – RM 7,500 per month",
    "workArrangements": {"data": [{"id": "1", "label": {"text": "On-site"}}]},
    "workTypes": ["Full time"],
    "listingDate": "2026-08-17T03:23:49Z",
    "listingDateDisplay": "4d ago",
    "teaser": "1-4 years of professional software development experience.",
}


def _client(handler):
    """Patch AsyncClient.get with a handler, bypassing the no-network guard."""
    return handler


@pytest.fixture
def api(monkeypatch):
    """Serve a canned payload and capture the request params."""
    seen = {}

    def serve(payload, status=200):
        async def fake_get(self, url, params=None, headers=None):
            seen["url"] = url
            seen["params"] = params
            return httpx.Response(
                status, content=json.dumps(payload).encode(),
                headers={"content-type": "application/json"},
                request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        return seen

    return serve


# --- normalize -------------------------------------------------------------

def test_normalize_maps_the_fields_the_scan_actually_reads():
    j = jobstreet.normalize(RAW)
    assert j["title"].startswith("Full-Stack Developer")
    assert j["company"] == "Hexa Business"
    assert j["location"] == "Kuala Lumpur City Centre, Kuala Lumpur"
    assert j["url"] == "https://my.jobstreet.com/job/94012364"
    assert j["salary"] == "RM 5,000 – RM 7,500 per month"
    assert j["work_type"] == "Full time"
    assert j["posted"] == "4d ago"
    assert "1-4 years" in j["teaser"]


def test_normalize_keeps_the_work_arrangement():
    """On-site vs Hybrid vs Remote is a dealbreaker for this user, not a detail."""
    assert jobstreet.normalize(RAW)["arrangement"] == "On-site"


def test_normalize_joins_multiple_arrangements():
    raw = dict(RAW, workArrangements={"data": [
        {"label": {"text": "Hybrid"}}, {"label": {"text": "Remote"}}]})
    assert jobstreet.normalize(raw)["arrangement"] == "Hybrid, Remote"


def test_normalize_falls_back_to_the_employer_name():
    raw = dict(RAW); raw.pop("companyName")
    assert jobstreet.normalize(raw)["company"] == "Hexa Business"


@pytest.mark.parametrize("missing", ["id", "title"])
def test_a_record_without_an_id_or_title_is_dropped(missing):
    raw = dict(RAW); raw.pop(missing)
    assert jobstreet.normalize(raw) is None


def test_a_record_without_any_employer_is_dropped():
    raw = dict(RAW); raw.pop("companyName"); raw.pop("employer")
    assert jobstreet.normalize(raw) is None


def test_missing_optional_fields_do_not_crash():
    j = jobstreet.normalize({"id": "1", "title": "Dev", "companyName": "X"})
    assert j["location"] == "" and j["salary"] == "" and j["arrangement"] == ""


def test_non_dict_input_is_dropped():
    assert jobstreet.normalize("not a job") is None


# --- search ----------------------------------------------------------------

async def test_search_returns_normalized_listings(api):
    api({"data": [RAW], "totalCount": 378})
    out = await jobstreet.search("python developer")
    assert len(out) == 1
    assert out[0]["company"] == "Hexa Business"


async def test_search_sends_the_country_site_key(api):
    seen = api({"data": [], "totalCount": 0})
    await jobstreet.search("x", where="Cheras")
    assert seen["params"]["siteKey"] == "MY-Main"
    assert seen["params"]["keywords"] == "x"
    assert seen["params"]["where"] == "Cheras"


def test_the_endpoint_is_the_search_api_not_a_job_page():
    """Job pages are 403 to any client; the search API is the whole point."""
    assert jobstreet.API.endswith("/api/jobsearch/v5/search")


async def test_page_size_is_clamped_to_something_the_api_accepts(api):
    seen = api({"data": []})
    await jobstreet.search("x", limit=9999)
    assert seen["params"]["pageSize"] == 100


async def test_an_http_error_yields_no_listings_rather_than_raising(api):
    """A board being down must degrade the scan, not abort it."""
    api({"error": "nope"}, status=503)
    assert await jobstreet.search("python") == []


async def test_a_malformed_payload_yields_no_listings(api):
    api({"unexpected": "shape"})
    assert await jobstreet.search("python") == []


async def test_a_transport_failure_yields_no_listings(monkeypatch):
    async def boom(self, url, params=None, headers=None):
        raise httpx.ConnectError("dns went away")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    assert await jobstreet.search("python") == []


async def test_search_many_merges_and_dedupes_by_url(api):
    other = dict(RAW, id="777", title="Backend Engineer")
    calls = []

    async def fake_get(self, url, params=None, headers=None):
        calls.append(params["keywords"])
        body = {"data": [RAW] if len(calls) == 1 else [RAW, other]}
        return httpx.Response(200, content=json.dumps(body).encode(),
                              headers={"content-type": "application/json"},
                              request=httpx.Request("GET", url))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    try:
        out = await jobstreet.search_many(["python developer", "backend developer"])
    finally:
        monkeypatch.undo()

    assert calls == ["python developer", "backend developer"]
    assert [j["title"] for j in out] == [RAW["title"], "Backend Engineer"]


# --- prompt block ----------------------------------------------------------

def test_prompt_block_is_empty_when_there_is_nothing_to_show():
    assert jobstreet.as_prompt_block([]) == ""


def test_prompt_block_carries_the_url_verbatim():
    block = jobstreet.as_prompt_block([jobstreet.normalize(RAW)])
    assert "https://my.jobstreet.com/job/94012364" in block


def test_prompt_block_shows_arrangement_and_salary():
    block = jobstreet.as_prompt_block([jobstreet.normalize(RAW)])
    assert "On-site" in block
    assert "RM 5,000" in block


def test_prompt_block_caps_how_many_listings_it_renders():
    many = [dict(jobstreet.normalize(RAW), url=f"u{i}") for i in range(50)]
    assert jobstreet.as_prompt_block(many, cap=3).count("Hexa Business") == 3


# --- scan integration ------------------------------------------------------

def test_the_scan_prompt_embeds_the_listings():
    prompt = scan.build_prompt([], [jobstreet.normalize(RAW)])
    assert "https://my.jobstreet.com/job/94012364" in prompt


def test_the_scan_prompt_tells_the_agent_not_to_fetch_them():
    """WebFetch on a JobStreet url returns 403, which the LIVE gate would read
    as 'cannot confirm open' and drop — losing every job on the board."""
    prompt = scan.build_prompt([], [jobstreet.normalize(RAW)])
    assert "Do NOT WebFetch these urls" in prompt
    assert "403" in prompt


def test_the_scan_prompt_grants_them_the_live_exemption():
    prompt = scan.build_prompt([], [jobstreet.normalize(RAW)])
    assert "WITHOUT being fetched" in prompt


def test_the_scan_prompt_survives_an_empty_provider_result():
    prompt = scan.build_prompt([], [])
    assert "JobStreet returned nothing" in prompt
    assert "{prefetched}" not in prompt


def test_the_prompt_still_lists_the_other_configured_sources():
    prompt = scan.build_prompt([], [jobstreet.normalize(RAW)])
    for u in config.SCAN_SOURCES:
        assert u in prompt


async def test_prefetch_is_skipped_when_no_queries_are_configured(monkeypatch):
    monkeypatch.setattr(config, "JOBSTREET_QUERIES", [])
    assert await scan._prefetch() == []


def test_a_truncated_listing_block_says_so(caplog):
    """A silent cap reads as 'the agent saw everything' when it did not."""
    many = [dict(jobstreet.normalize(RAW), url=f"u{i}") for i in range(40)]
    with caplog.at_level("INFO", logger="career-agent.jobstreet"):
        jobstreet.as_prompt_block(many, cap=25)
    assert any("25 of 40" in r.getMessage() for r in caplog.records)
