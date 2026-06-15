import importlib

import pytest

import agent
import config
import jobs_store
import preferences


@pytest.fixture
def store_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS_STORE", tmp_path / "jobs_seen.json")
    importlib.reload(jobs_store)
    importlib.reload(preferences)
    return jobs_store


def _decide(js, title, state, reason=None):
    jid = js.record({"title": title, "company": "C", "url": f"https://x.io/{title}"},
                    "offered")
    js.set_decision(jid, state, reason)
    return jid


def _fake_reply(text):
    async def _run(prompt, session_id=None, files=None):
        return text, "sess-1"
    return _run


def test_needs_synthesis_false_when_no_decisions(store_in_tmp):
    assert preferences.needs_synthesis() is False


def test_needs_synthesis_true_with_fresh_decisions(store_in_tmp):
    _decide(store_in_tmp, "A", "skipped", "onsite")
    assert preferences.needs_synthesis() is True


def test_needs_synthesis_false_after_marking(store_in_tmp):
    _decide(store_in_tmp, "A", "skipped", "onsite")
    store_in_tmp.mark_synthesis()
    assert preferences.needs_synthesis() is False


async def test_run_synthesis_returns_summary_line(store_in_tmp, monkeypatch):
    _decide(store_in_tmp, "A", "skipped", "onsite")
    monkeypatch.setattr(agent, "run_turn",
                        _fake_reply("Noticed you skip onsite roles — weighting lower.\n"))
    note = await preferences.run_synthesis()
    assert note == "Noticed you skip onsite roles — weighting lower."
    assert store_in_tmp.last_synthesis_at() is not None  # marked after success


async def test_run_synthesis_no_change_returns_none(store_in_tmp, monkeypatch):
    _decide(store_in_tmp, "A", "skipped", "onsite")
    monkeypatch.setattr(agent, "run_turn", _fake_reply("NO_CHANGE"))
    assert await preferences.run_synthesis() is None


async def test_run_synthesis_skips_when_not_needed(store_in_tmp, monkeypatch):
    called = {"n": 0}

    async def _spy(prompt, session_id=None, files=None):
        called["n"] += 1
        return "x", "s"

    monkeypatch.setattr(agent, "run_turn", _spy)
    assert await preferences.run_synthesis() is None  # no decisions
    assert called["n"] == 0


async def test_run_synthesis_agent_error_returns_none_unmarked(store_in_tmp, monkeypatch):
    _decide(store_in_tmp, "A", "skipped", "onsite")

    async def _boom(prompt, session_id=None, files=None):
        raise RuntimeError("cli down")

    monkeypatch.setattr(agent, "run_turn", _boom)
    assert await preferences.run_synthesis() is None
    assert store_in_tmp.last_synthesis_at() is None  # not marked, so it retries
