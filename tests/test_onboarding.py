# tests/test_onboarding.py
import json

import onboarding


def _setup_memory(tmp_path, monkeypatch):
    """Point onboarding's path lookups at a temp dir and return its memory dirs."""
    import config
    mem = tmp_path / "memory"
    exps = mem / "experiences"
    exps.mkdir(parents=True)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "MEMORY_DIR", mem)
    monkeypatch.setattr(config, "EXPERIENCES_DIR", exps)
    return mem, exps


def test_is_fresh_when_memory_empty(tmp_path, monkeypatch):
    _setup_memory(tmp_path, monkeypatch)
    assert onboarding.is_fresh() is True


def test_not_fresh_when_profile_populated(tmp_path, monkeypatch):
    mem, _ = _setup_memory(tmp_path, monkeypatch)
    (mem / "profile.md").write_text("# Profile\n" + "Real career detail. " * 10,
                                    encoding="utf-8")
    assert onboarding.is_fresh() is False


def test_not_fresh_when_experiences_exist(tmp_path, monkeypatch):
    _, exps = _setup_memory(tmp_path, monkeypatch)
    (exps / "a-job.md").write_text("title: x", encoding="utf-8")
    assert onboarding.is_fresh() is False


def test_tiny_stub_profile_still_fresh(tmp_path, monkeypatch):
    mem, _ = _setup_memory(tmp_path, monkeypatch)
    (mem / "profile.md").write_text("hi", encoding="utf-8")
    assert onboarding.is_fresh() is True


def test_not_fresh_when_goals_populated(tmp_path, monkeypatch):
    mem, _ = _setup_memory(tmp_path, monkeypatch)
    (mem / "goals.md").write_text("# Goals\n" + "Target SRE roles in KL. " * 8,
                                  encoding="utf-8")
    assert onboarding.is_fresh() is False


def test_status_defaults_not_started(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    assert onboarding.status() == "not_started"


def test_set_status_in_progress_records_started_at(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    onboarding.set_status("in_progress")
    assert onboarding.status() == "in_progress"
    data = json.loads((tmp_path / "data" / "onboarding.json").read_text())
    assert data["started_at"]


def test_set_status_done_keeps_started_and_adds_completed(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    onboarding.set_status("in_progress")
    started = json.loads((tmp_path / "data" / "onboarding.json").read_text())["started_at"]
    onboarding.set_status("done")
    data = json.loads((tmp_path / "data" / "onboarding.json").read_text())
    assert onboarding.status() == "done"
    assert data["started_at"] == started
    assert data["completed_at"]


def test_status_corrupt_file_is_not_started(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "onboarding.json").write_text("{not json", encoding="utf-8")
    assert onboarding.status() == "not_started"


def test_set_status_rejects_invalid_state(tmp_path, monkeypatch):
    import config
    import pytest
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    with pytest.raises(ValueError):
        onboarding.set_status("bogus")


def test_strip_marker_absent():
    clean, done = onboarding.strip_complete_marker("just a normal reply")
    assert clean == "just a normal reply"
    assert done is False


def test_strip_marker_present():
    clean, done = onboarding.strip_complete_marker(
        "All set! Upload your resume next.\n" + onboarding.COMPLETE_MARKER)
    assert onboarding.COMPLETE_MARKER not in clean
    assert clean == "All set! Upload your resume next."
    assert done is True


def test_strip_marker_multiple_occurrences():
    text = f"{onboarding.COMPLETE_MARKER} mid {onboarding.COMPLETE_MARKER} end"
    clean, done = onboarding.strip_complete_marker(text)
    assert onboarding.COMPLETE_MARKER not in clean
    assert done is True


def test_kickoff_prompt_mentions_marker_and_files():
    assert onboarding.COMPLETE_MARKER in onboarding.ONBOARDING_KICKOFF
    assert "profile.md" in onboarding.ONBOARDING_KICKOFF
    assert "goals.md" in onboarding.ONBOARDING_KICKOFF
