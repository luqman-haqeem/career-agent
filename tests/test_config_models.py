import importlib

import config


def _reload(monkeypatch, **env):
    for k in ("SCAN_MODEL", "RESUME_MODEL", "CRITIQUE_MODEL",
              "CLASSIFIER_MODEL", "OPENCODE_MODEL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(config)
    return config


def test_model_for_falls_back_to_default(monkeypatch):
    cfg = _reload(monkeypatch)
    assert cfg.model_for("scan") == cfg.OPENCODE_MODEL
    assert cfg.model_for("resume") == cfg.OPENCODE_MODEL
    assert cfg.model_for("critique") == cfg.OPENCODE_MODEL
    assert cfg.model_for("classifier") == cfg.OPENCODE_MODEL
    assert cfg.model_for("default") == cfg.OPENCODE_MODEL


def test_model_for_uses_override(monkeypatch):
    cfg = _reload(monkeypatch, SCAN_MODEL="openrouter/x/scan-model")
    assert cfg.model_for("scan") == "openrouter/x/scan-model"
    assert cfg.model_for("resume") == cfg.OPENCODE_MODEL


def test_routing_inactive_when_unset(monkeypatch):
    cfg = _reload(monkeypatch)
    assert cfg.routing_active() is False


def test_routing_active_when_critique_set(monkeypatch):
    cfg = _reload(monkeypatch, CRITIQUE_MODEL="openrouter/x/crit")
    assert cfg.routing_active() is True


def test_routing_active_when_resume_set(monkeypatch):
    cfg = _reload(monkeypatch, RESUME_MODEL="openrouter/x/res")
    assert cfg.routing_active() is True


def test_scan_model_does_not_activate_routing(monkeypatch):
    # scan has its own path; setting it must NOT switch on the chat classifier.
    cfg = _reload(monkeypatch, SCAN_MODEL="openrouter/x/scan")
    assert cfg.routing_active() is False
