"""Two silent-failure holes: unlogged credentials, and unanswered crashes.

The bot token lives in the URL PATH of every Telegram API call, and httpx logs
request URLs at INFO — so the token was printed on every single call, sitting
in `docker logs` for anyone with shell access. Separately, no error handler was
registered, so an exception escaping a handler was logged to stdout and the
user just never got a reply.
"""
import asyncio
import io
import logging

import pytest
from telegram.error import NetworkError, TimedOut

import bot

TOKEN_URL = ("https://api.telegram.org/"
             "botREDACTED-ROTATED-TOKEN/sendMessage")


# --- log redaction ---------------------------------------------------------

def _emit(*args, **kw):
    """Log one record through the filter and return the rendered line."""
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(bot._RedactingFilter())
    lg = logging.getLogger("redaction-probe")
    lg.handlers = [h]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.info(*args, **kw)
    return buf.getvalue()


def test_the_token_is_redacted_when_it_is_a_log_argument():
    """httpx passes the URL as %s, so scrubbing record.msg alone missed it."""
    out = _emit('HTTP Request: %s %s "%s %d %s"', "POST", TOKEN_URL,
                "HTTP/1.1", 200, "OK")
    assert "AAHw45" not in out
    assert "/bot<redacted>/sendMessage" in out


def test_the_token_is_redacted_inside_the_format_string_too():
    assert "AAHw45" not in _emit("calling " + TOKEN_URL)


def test_dict_style_args_are_redacted():
    out = _emit("%(url)s failed", {"url": TOKEN_URL})
    assert "AAHw45" not in out


class _UrlObject:
    """Stands in for httpx.URL — NOT a str, which is the whole point."""

    def __str__(self):
        return TOKEN_URL


def test_the_token_is_redacted_when_the_url_is_not_a_string():
    """Regression, caught in production, not by this file.

    The first version of the filter scrubbed record.msg and any str args. It
    passed every test here and still printed the token on every API call:
    httpx logs an httpx.URL OBJECT, which is neither.
    """
    out = _emit('HTTP Request: %s %s "%s %d %s"', "POST", _UrlObject(),
                "HTTP/1.1", 200, "OK")
    assert "AAHw45" not in out
    assert "/bot<redacted>/sendMessage" in out


def test_a_numeric_arg_still_renders_after_redaction():
    """Scrubbing folds args into the message — %d must not break."""
    out = _emit("%s took %d ms", _UrlObject(), 42)
    assert "took 42 ms" in out
    assert "AAHw45" not in out


def test_a_bad_format_string_does_not_crash_the_filter():
    assert _emit("%d oops", "not-a-number") is not None


def test_the_plain_token_pattern_alone_did_not_cover_the_url():
    """Regression: 'bot' and the leading digit are both word characters, so
    the \\b-anchored token pattern never matched inside a URL path."""
    url_pattern = bot._SECRET_PATTERNS[0]
    others = [p for p in bot._SECRET_PATTERNS[1:] if p.search(TOKEN_URL)]
    assert url_pattern.search(TOKEN_URL), "the /bot… pattern must match"
    assert others == [], "if another pattern now covers it, drop the special case"


def test_an_api_key_in_a_log_line_is_redacted():
    assert "sk-or-v1" not in _emit("using key sk-or-v1-9f8e7d6c5b4a39281706abc")


def test_ordinary_log_lines_are_untouched():
    assert "scan finished: 5 matches" in _emit("scan finished: 5 matches")


def test_non_string_args_survive_the_filter():
    """Ints and objects must pass through, not be stringified or dropped."""
    assert "count 42" in _emit("count %d", 42)


def test_the_redacting_filter_is_installed_on_the_root_handlers():
    """A Filter on a LOGGER is not consulted for records propagating up from
    child loggers — httpx's record would have sailed straight past."""
    handlers = logging.getLogger().handlers
    assert any(any(isinstance(f, bot._RedactingFilter) for f in h.filters)
               for h in handlers)


# --- error handler ---------------------------------------------------------

class _Chat:
    id = 4242


class _Update:
    effective_chat = _Chat()


class _Bot:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, chat_id=None, text=None, **kw):
        if self.fail:
            raise RuntimeError("telegram is down too")
        self.sent.append((chat_id, text))


class _Ctx:
    def __init__(self, error, fail=False):
        self.error = error
        self.bot = _Bot(fail)


def test_the_handler_is_registered():
    import inspect
    assert "app.add_error_handler(on_error)" in inspect.getsource(bot.main)


def test_a_crash_during_a_chat_tells_the_user():
    """The whole point: the user used to get silence."""
    ctx = _Ctx(RuntimeError("resume render blew up"))
    asyncio.run(bot.on_error(_Update(), ctx))
    assert len(ctx.bot.sent) == 1
    chat_id, text = ctx.bot.sent[0]
    assert chat_id == 4242
    assert "resume render blew up" in text


def test_the_message_to_the_user_is_scrubbed():
    ctx = _Ctx(RuntimeError(f"POST {TOKEN_URL} failed"))
    asyncio.run(bot.on_error(_Update(), ctx))
    assert "AAHw45" not in ctx.bot.sent[0][1]


def test_a_polling_hiccup_with_no_chat_does_not_message_anyone(caplog):
    """Nine Bad Gateways in three days — nobody is waiting on those."""
    ctx = _Ctx(NetworkError("Bad Gateway"))
    with caplog.at_level(logging.WARNING):
        asyncio.run(bot.on_error("not-an-update", ctx))
    assert ctx.bot.sent == []
    assert any("Bad Gateway" in r.getMessage() for r in caplog.records)


def test_a_polling_hiccup_is_logged_without_a_traceback(caplog):
    """These were dumping full stacks and drowning the real signal."""
    with caplog.at_level(logging.WARNING):
        asyncio.run(bot.on_error("not-an-update", _Ctx(TimedOut())))
    assert all(r.exc_info is None for r in caplog.records)


def test_a_network_error_that_DID_hit_a_user_still_tells_them():
    """A hiccup with a chat attached means a real message went unanswered."""
    ctx = _Ctx(NetworkError("Bad Gateway"))
    asyncio.run(bot.on_error(_Update(), ctx))
    assert len(ctx.bot.sent) == 1


def test_a_crash_with_no_chat_is_logged_with_its_traceback(caplog):
    with caplog.at_level(logging.ERROR):
        asyncio.run(bot.on_error(None, _Ctx(ValueError("boom"))))
    assert any(r.exc_info for r in caplog.records)


def test_failing_to_notify_the_user_does_not_raise(caplog):
    """The error path must not become its own error."""
    ctx = _Ctx(RuntimeError("original"), fail=True)
    with caplog.at_level(logging.ERROR):
        asyncio.run(bot.on_error(_Update(), ctx))  # must not raise
    assert any("Could not tell the user" in r.getMessage() for r in caplog.records)
