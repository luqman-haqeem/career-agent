import json
from pathlib import Path

import config


def test_opencode_model_has_openrouter_default(monkeypatch):
    # Verify the hardcoded code default, independent of any local .env that may
    # set OPENCODE_MODEL: neutralize load_dotenv so reload can't re-inject it.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    import importlib
    importlib.reload(config)
    assert config.OPENCODE_MODEL == "openrouter/google/gemini-2.5-flash-lite"


def test_opencode_json_denies_bash():
    data = json.loads(Path("opencode.json").read_text())
    # bash must be denied; file tools + web allowed.
    perms = data.get("permission", {})
    assert perms.get("bash") == "deny"


def test_opencode_json_is_valid_schema_doc():
    data = json.loads(Path("opencode.json").read_text())
    assert "$schema" in data


def test_claude_config_removed():
    import importlib
    importlib.reload(config)
    assert not hasattr(config, "AI_BACKEND")
    assert not hasattr(config, "CLAUDE_BIN")
    assert not hasattr(config, "MODEL")
