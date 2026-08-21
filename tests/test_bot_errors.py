"""User-facing error text: useful to the operator, safe to send over Telegram."""
import bot


def test_a_plain_error_reaches_the_user_intact():
    """Diagnostic value is the point — this exact string identified a real bug."""
    e = RuntimeError("404 No endpoints found that support image input")
    assert bot._safe_error(e) == "404 No endpoints found that support image input"


def test_an_api_key_is_redacted():
    e = RuntimeError("rejected key sk-or-v1-9f8e7d6c5b4a39281706abcdef012345")
    out = bot._safe_error(e)
    assert "sk-or-v1" not in out
    assert "<redacted>" in out


def test_a_bearer_header_is_redacted():
    e = RuntimeError("Authorization: Bearer abcdef0123456789abcdef0123456789")
    out = bot._safe_error(e)
    assert "abcdef0123456789" not in out


def test_a_telegram_bot_token_is_redacted():
    e = RuntimeError("telegram 1234567890:AAHfakefakefakefakefakefakefakefake12 invalid")
    out = bot._safe_error(e)
    assert "AAHfake" not in out


def test_an_env_style_assignment_is_redacted_but_keeps_its_name():
    """Knowing WHICH variable failed is useful; its value is not."""
    e = RuntimeError("OPENROUTER_API_KEY=sk-or-v1-9f8e7d6c5b4a3928 rejected")
    out = bot._safe_error(e)
    assert "OPENROUTER_API_KEY" in out
    assert "sk-or-v1" not in out


def test_long_errors_are_truncated():
    out = bot._safe_error(RuntimeError("x" * 5000))
    assert len(out) < 500
    assert "truncated" in out


def test_an_empty_error_still_says_something():
    assert bot._safe_error(ValueError("")) == "ValueError"


def test_every_user_facing_error_path_is_scrubbed():
    """A new error path must not reintroduce a raw f-string interpolation."""
    src = (bot.Path(bot.__file__).read_text(encoding="utf-8")
           if hasattr(bot, "Path") else open(bot.__file__, encoding="utf-8").read())
    import re
    # Any '{e}' left in a message string means an unscrubbed exception.
    raw = [ln.strip() for ln in src.splitlines()
           if re.search(r'f"[^"]*\{e\}', ln)]
    assert raw == [], f"unscrubbed exception interpolation: {raw}"
