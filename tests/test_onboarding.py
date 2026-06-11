# tests/test_onboarding.py
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
