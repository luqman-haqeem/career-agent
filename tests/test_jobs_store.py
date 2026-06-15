import importlib

import config
import jobs_store


def _fresh_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS_STORE", tmp_path / "jobs_seen.json")
    importlib.reload(jobs_store)  # pick up patched path if module cached it
    return jobs_store


def test_job_id_is_stable_and_url_normalized(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    a = {"title": "DevOps Engineer", "company": "Acme", "url": "https://x.io/jobs/9/"}
    b = {"title": "DevOps Engineer", "company": "Acme", "url": "HTTPS://x.io/jobs/9"}
    assert js.job_id(a) == js.job_id(b)  # case + trailing slash invariant


def test_job_id_falls_back_to_company_title_without_url(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    a = {"title": "SRE", "company": "Acme", "url": ""}
    b = {"title": "sre", "company": "ACME", "url": ""}
    assert js.job_id(a) == js.job_id(b)


def test_record_then_seen_and_get(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    job = {"title": "SRE", "company": "Acme", "location": "KL",
           "url": "https://x.io/1", "fit_score": 8}
    jid = js.record(job, "offered")
    assert jid in js.seen_ids()
    entry = js.get(jid)
    assert entry["state"] == "offered"
    assert entry["company"] == "Acme"
    assert "first_seen" in entry


def test_set_state_updates_and_persists(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    jid = js.record({"title": "SRE", "company": "Acme", "url": "https://x.io/1"}, "offered")
    js.set_state(jid, "applied")
    assert js.get(jid)["state"] == "applied"
    # reload-from-disk check
    js2 = _fresh_store(tmp_path, monkeypatch)
    assert js2.get(jid)["state"] == "applied"


def test_recent_labels(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    js.record({"title": "SRE", "company": "Acme", "url": "https://x.io/1"}, "offered")
    js.record({"title": "DevOps", "company": "Beta", "url": "https://x.io/2"}, "offered")
    labels = js.recent_labels()
    assert "SRE @ Acme" in labels
    assert "DevOps @ Beta" in labels


def test_corrupt_file_returns_empty(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    (tmp_path / "jobs_seen.json").write_text("{not valid json", encoding="utf-8")
    assert js.seen_ids() == set()


def test_wrong_shape_returns_empty(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    (tmp_path / "jobs_seen.json").write_text('["a", "b"]', encoding="utf-8")
    assert js.seen_ids() == set()


def test_record_round_trips_skip_reasons(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    job = {"title": "SRE", "company": "Acme", "url": "https://x.io/1",
           "skip_reasons": ["Frontend role", "Needs 8y", "Onsite Penang"]}
    jid = js.record(job, "offered")
    assert js.get(jid)["skip_reasons"] == ["Frontend role", "Needs 8y", "Onsite Penang"]


def test_record_skip_reasons_defaults_to_empty(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    jid = js.record({"title": "SRE", "company": "Acme", "url": "https://x.io/1"}, "offered")
    assert js.get(jid)["skip_reasons"] == []


def test_set_decision_records_state_reason_and_timestamp(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    jid = js.record({"title": "SRE", "company": "Acme", "url": "https://x.io/1"}, "offered")
    js.set_decision(jid, "skipped", "wrong domain")
    entry = js.get(jid)
    assert entry["state"] == "skipped"
    assert entry["reason"] == "wrong domain"
    assert "decided_at" in entry


def test_set_decision_without_reason_omits_reason(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    jid = js.record({"title": "SRE", "company": "Acme", "url": "https://x.io/1"}, "offered")
    js.set_decision(jid, "applied")
    entry = js.get(jid)
    assert entry["state"] == "applied"
    assert "reason" not in entry
    assert "decided_at" in entry


def test_decisions_returns_decided_only_newest_first(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    a = js.record({"title": "A", "company": "C1", "url": "https://x.io/1"}, "offered")
    b = js.record({"title": "B", "company": "C2", "url": "https://x.io/2"}, "offered")
    js.record({"title": "C", "company": "C3", "url": "https://x.io/3"}, "offered")  # stays offered
    js.set_decision(a, "applied")
    js.set_decision(b, "skipped", "too senior")
    decs = js.decisions()
    states = [d["state"] for d in decs]
    assert "offered" not in states
    assert {d["id"] for d in decs} == {a, b}
    assert decs[0]["id"] == b  # b decided after a -> newest first
    assert decs[0]["reason"] == "too senior"


def test_synthesis_marker_roundtrips(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    assert js.last_synthesis_at() is None
    js.mark_synthesis()
    assert js.last_synthesis_at() is not None
    js2 = _fresh_store(tmp_path, monkeypatch)
    assert js2.last_synthesis_at() is not None


def test_record_does_not_clobber_skip_reasons_on_rerecord(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    job = {"title": "SRE", "company": "Acme", "url": "https://x.io/1",
           "skip_reasons": ["Frontend role", "Needs 8y"]}
    jid = js.record(job, "offered")
    # Re-record the SAME job without skip_reasons -> must keep the originals.
    js.record({"title": "SRE", "company": "Acme", "url": "https://x.io/1"}, "offered")
    assert js.get(jid)["skip_reasons"] == ["Frontend role", "Needs 8y"]


def test_set_decision_clears_stale_reason_on_redecision(tmp_path, monkeypatch):
    js = _fresh_store(tmp_path, monkeypatch)
    jid = js.record({"title": "SRE", "company": "Acme", "url": "https://x.io/1"}, "offered")
    js.set_decision(jid, "skipped", "too senior")
    js.set_decision(jid, "applied")  # re-decide without a reason
    entry = js.get(jid)
    assert entry["state"] == "applied"
    assert "reason" not in entry
