import config


def test_vision_api_model_strips_openrouter_prefix(monkeypatch):
    monkeypatch.setattr(config, "VISION_MODEL", "openrouter/google/gemini-2.5-flash-lite")
    assert config.vision_api_model() == "google/gemini-2.5-flash-lite"


def test_vision_api_model_falls_back_to_default_model(monkeypatch):
    monkeypatch.setattr(config, "VISION_MODEL", "")
    monkeypatch.setattr(config, "OPENCODE_MODEL", "openrouter/x/y")
    assert config.vision_api_model() == "x/y"


def test_vision_api_model_leaves_bare_slug_untouched(monkeypatch):
    monkeypatch.setattr(config, "VISION_MODEL", "anthropic/claude-3")
    assert config.vision_api_model() == "anthropic/claude-3"
