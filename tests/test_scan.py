import scan


def test_parse_matches_clean_array():
    text = '[{"title":"SRE","company":"Acme","url":"https://x.io/1","fit_score":8,' \
           '"why_fit":"aws","why_aligns":"sre pivot","location":"KL"}]'
    out = scan.parse_matches(text)
    assert len(out) == 1
    assert out[0]["company"] == "Acme"


def test_parse_matches_strips_fences_and_prose():
    text = ("Here are the matches:\n```json\n"
            '[{"title":"DevOps","company":"Beta","url":"https://x.io/2","fit_score":7,'
            '"why_fit":"docker","why_aligns":"hybrid","location":"Cheras"}]'
            "\n```\nHope that helps!")
    out = scan.parse_matches(text)
    assert len(out) == 1
    assert out[0]["title"] == "DevOps"


def test_parse_matches_empty_array():
    assert scan.parse_matches("[]") == []


def test_parse_matches_garbage_returns_empty():
    assert scan.parse_matches("sorry, I could not find anything") == []


def test_valid_match_requires_core_fields():
    good = {"title": "SRE", "company": "Acme", "url": "https://x.io/1", "fit_score": 8}
    assert scan.valid_match(good)
    assert not scan.valid_match({"title": "", "company": "Acme", "url": "https://x.io/1"})
    assert not scan.valid_match({"title": "SRE", "company": "Acme", "url": ""})


def test_coerce_fit_score_from_string():
    out = scan.parse_matches('[{"title":"SRE","company":"Acme","url":"https://x.io/1",'
                             '"fit_score":"9"}]')
    assert out[0]["fit_score"] == 9


def test_parse_matches_prose_with_earlier_bracket():
    text = ('Matches [top results]:\n'
            '[{"title":"SRE","company":"Acme","url":"https://x.io/1","fit_score":8,'
            '"why_fit":"a","why_aligns":"b","location":"KL"}]')
    out = scan.parse_matches(text)
    assert len(out) == 1
    assert out[0]["company"] == "Acme"


import importlib

import pytest

import agent
import config


@pytest.fixture
def store_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS_STORE", tmp_path / "jobs_seen.json")
    import jobs_store
    importlib.reload(jobs_store)
    importlib.reload(scan)
    return jobs_store


def _fake_reply(jobs_json):
    async def _run(prompt, session_id=None, files=None):
        return jobs_json, "sess-1"
    return _run


async def test_run_scan_records_and_returns(store_in_tmp, monkeypatch):
    payload = ('[{"title":"SRE","company":"Acme","location":"KL",'
               '"url":"https://x.io/1","fit_score":8,"why_fit":"aws","why_aligns":"pivot"}]')
    monkeypatch.setattr(agent, "run_turn", _fake_reply(payload))
    matches = await scan.run_scan()
    assert len(matches) == 1
    assert matches[0]["id"] in store_in_tmp.seen_ids()


async def test_run_scan_dedupes_on_second_run(store_in_tmp, monkeypatch):
    payload = ('[{"title":"SRE","company":"Acme","location":"KL",'
               '"url":"https://x.io/1","fit_score":8,"why_fit":"aws","why_aligns":"pivot"}]')
    monkeypatch.setattr(agent, "run_turn", _fake_reply(payload))
    first = await scan.run_scan()
    assert len(first) == 1
    second = await scan.run_scan()  # same job returned again
    assert second == []


async def test_run_scan_caps_to_max(store_in_tmp, monkeypatch):
    monkeypatch.setattr(config, "MAX_MATCHES_PER_SCAN", 1)
    payload = (
        '[{"title":"A","company":"C1","url":"https://x.io/1","fit_score":8},'
        '{"title":"B","company":"C2","url":"https://x.io/2","fit_score":7}]'
    )
    monkeypatch.setattr(agent, "run_turn", _fake_reply(payload))
    matches = await scan.run_scan()
    assert len(matches) == 1
    assert len(store_in_tmp.seen_ids()) == 1  # dropped job not consumed from the store


async def test_run_scan_retries_once_then_raises(store_in_tmp, monkeypatch):
    calls = {"n": 0}

    async def _boom(prompt, session_id=None, files=None):
        calls["n"] += 1
        raise RuntimeError("cli down")

    monkeypatch.setattr(agent, "run_turn", _boom)
    with pytest.raises(scan.ScanError):
        await scan.run_scan()
    assert calls["n"] == 2  # initial + one retry


async def test_run_scan_dedupes_within_batch(store_in_tmp, monkeypatch):
    # Same URL twice in one reply -> collapsed to a single match.
    payload = (
        '[{"title":"SRE","company":"Acme","url":"https://x.io/1","fit_score":8},'
        '{"title":"SRE (dup)","company":"Acme","url":"https://x.io/1","fit_score":7}]'
    )
    monkeypatch.setattr(agent, "run_turn", _fake_reply(payload))
    matches = await scan.run_scan()
    assert len(matches) == 1
