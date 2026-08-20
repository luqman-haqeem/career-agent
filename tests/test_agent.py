import asyncio
import json
import os

import pytest

import agent


def _line(**ev):
    return json.dumps(ev)


def test_parse_joins_text_parts_in_first_seen_order():
    out = "\n".join([
        _line(sessionID="ses_abc", part={"type": "text", "id": "p1", "text": "Hello"}),
        _line(sessionID="ses_abc", part={"type": "text", "id": "p2", "text": " world"}),
    ])
    reply, sid = agent._opencode_parse(out, None)
    assert reply == "Hello world"
    assert sid == "ses_abc"


def test_parse_latest_snapshot_per_part_wins():
    out = "\n".join([
        _line(part={"type": "text", "id": "p1", "text": "Hel"}),
        _line(part={"type": "text", "id": "p1", "text": "Hello"}),
    ])
    reply, _ = agent._opencode_parse(out, None)
    assert reply == "Hello"


def test_parse_ignores_blank_and_malformed_lines():
    out = "\n".join([
        "",
        "not json",
        _line(part={"type": "text", "id": "p1", "text": "ok"}),
        "{bad json",
    ])
    reply, _ = agent._opencode_parse(out, None)
    assert reply == "ok"


def test_parse_falls_back_to_given_session_when_absent():
    out = _line(part={"type": "text", "id": "p1", "text": "hi"})
    _, sid = agent._opencode_parse(out, "ses_prev")
    assert sid == "ses_prev"


def test_parse_skips_non_text_parts():
    out = "\n".join([
        _line(part={"type": "tool", "id": "t1", "text": "IGNORED"}),
        _line(part={"type": "text", "id": "p1", "text": "kept"}),
    ])
    reply, _ = agent._opencode_parse(out, None)
    assert reply == "kept"


def test_run_turn_uses_opencode_path(monkeypatch):
    calls = {}

    async def fake_opencode(user_message, session_id=None, files=None, model=None):
        calls["msg"] = user_message
        return "reply-text", "ses_new"

    monkeypatch.setattr(agent, "_opencode_run_turn", fake_opencode)
    import asyncio
    reply, sid = asyncio.run(agent.run_turn("hi", session_id="ses_old"))
    assert reply == "reply-text"
    assert sid == "ses_new"
    assert calls["msg"] == "hi"


def test_build_args_uses_passed_model():
    args = agent._opencode_build_args("hi", None, None, model="openrouter/x/y")
    assert "--model" in args
    assert args[args.index("--model") + 1] == "openrouter/x/y"


def test_build_args_falls_back_to_default_model(monkeypatch):
    monkeypatch.setattr(agent.config, "OPENCODE_MODEL", "openrouter/default/m")
    args = agent._opencode_build_args("hi", None, None, model=None)
    assert args[args.index("--model") + 1] == "openrouter/default/m"


def test_run_turn_threads_model(monkeypatch):
    calls = {}

    async def fake_opencode(user_message, session_id=None, files=None, model=None):
        calls["model"] = model
        return "r", "s"

    monkeypatch.setattr(agent, "_opencode_run_turn", fake_opencode)
    import asyncio
    asyncio.run(agent.run_turn("hi", model="openrouter/x/y"))
    assert calls["model"] == "openrouter/x/y"


def test_no_claude_backend_symbols():
    # The Claude path must be fully removed.
    assert not hasattr(agent, "_claude_cli_run_turn")
    assert not hasattr(agent, "ALLOWED_TOOLS")


def _spy_on_spawn(monkeypatch, pids):
    """Record the pid of each spawned child so a test can check it was reaped."""
    real = asyncio.create_subprocess_exec

    async def spy(*a, **k):
        proc = await real(*a, **k)
        pids.append(proc.pid)
        return proc

    monkeypatch.setattr(agent.asyncio, "create_subprocess_exec", spy)


def test_invoke_returns_output_when_under_timeout(monkeypatch):
    monkeypatch.setattr(agent, "_opencode_build_args", lambda *a, **k: ["sh", "-c", "echo hello"])
    monkeypatch.setattr(agent.config, "OPENCODE_TIMEOUT", 30)
    rc, out, err = asyncio.run(agent._opencode_invoke("hi", None, None))
    assert rc == 0
    assert out.strip() == "hello"


def test_invoke_kills_a_run_that_exceeds_the_timeout(monkeypatch):
    pids = []
    _spy_on_spawn(monkeypatch, pids)
    monkeypatch.setattr(agent, "_opencode_build_args", lambda *a, **k: ["sleep", "60"])
    monkeypatch.setattr(agent.config, "OPENCODE_TIMEOUT", 0.5)

    with pytest.raises(RuntimeError, match="exceeded"):
        asyncio.run(agent._opencode_invoke("hi", None, None))

    # The child must be gone, not left running like the 41-day scan that
    # motivated this timeout.
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)


def test_invoke_kills_grandchildren_too(monkeypatch):
    """A helper spawned by the run dies with it (process-group kill)."""
    pids = []
    _spy_on_spawn(monkeypatch, pids)
    monkeypatch.setattr(
        agent,
        "_opencode_build_args",
        lambda *a, **k: ["sh", "-c", "sleep 60 & echo $! >&2; wait"],
    )
    monkeypatch.setattr(agent.config, "OPENCODE_TIMEOUT", 0.5)

    with pytest.raises(RuntimeError):
        asyncio.run(agent._opencode_invoke("hi", None, None))

    # The shell got its own session, so the group kill reached the sleep too.
    assert not os.path.exists(f"/proc/{pids[0]}")


def test_timeout_does_not_trigger_the_stale_session_retry(monkeypatch):
    """A hung run must fail outright — retrying would double the hang."""
    calls = []

    async def hanging_invoke(user_message, session_id, files, model=None):
        calls.append(session_id)
        raise RuntimeError("opencode exceeded 1800s and was killed")

    monkeypatch.setattr(agent, "_opencode_invoke", hanging_invoke)
    with pytest.raises(RuntimeError, match="exceeded"):
        asyncio.run(agent._opencode_run_turn("hi", session_id="ses_old"))
    assert len(calls) == 1


def test_opencode_timeout_is_configured_and_positive():
    assert isinstance(agent.config.OPENCODE_TIMEOUT, int)
    assert agent.config.OPENCODE_TIMEOUT > 0


def test_run_turn_threads_model_through_retry(monkeypatch):
    seen = []

    async def fake_invoke(user_message, session_id, files, model=None):
        seen.append((session_id, model))
        if len(seen) == 1:
            return 1, "", "stale session"   # first call fails -> triggers retry
        return 0, json.dumps({"part": {"type": "text", "id": "p1", "text": "ok"}}), ""

    monkeypatch.setattr(agent, "_opencode_invoke", fake_invoke)
    import asyncio
    reply, _ = asyncio.run(agent._opencode_run_turn("hi", session_id="ses_old", model="openrouter/x/y"))
    assert reply == "ok"
    assert len(seen) == 2
    assert seen[0][1] == "openrouter/x/y"   # model on first invoke
    assert seen[1][1] == "openrouter/x/y"   # model survived the retry
    assert seen[1][0] is None               # retry uses a fresh (None) session


def test_empty_reply_raises_instead_of_returning_a_placeholder(monkeypatch):
    """Exit 0 with no assistant text is a failure, not a reply.

    A photo sent to a text-only model made opencode exit 0 having written only
    the user message; the old placeholder made that look like a normal answer.
    """
    async def fake_invoke(user_message, session_id, files, model=None):
        return 0, "", "No endpoints found that support image input"

    monkeypatch.setattr(agent, "_opencode_invoke", fake_invoke)
    with pytest.raises(RuntimeError, match="without an assistant reply"):
        asyncio.run(agent._opencode_run_turn("hi"))


def test_empty_reply_error_carries_the_provider_detail(monkeypatch):
    async def fake_invoke(user_message, session_id, files, model=None):
        return 0, "", "404 image input unsupported"

    monkeypatch.setattr(agent, "_opencode_invoke", fake_invoke)
    with pytest.raises(RuntimeError, match="image input unsupported"):
        asyncio.run(agent._opencode_run_turn("hi"))


def test_whitespace_only_reply_is_treated_as_empty(monkeypatch):
    async def fake_invoke(user_message, session_id, files, model=None):
        return 0, json.dumps({"part": {"type": "text", "id": "p1", "text": "   "}}), ""

    monkeypatch.setattr(agent, "_opencode_invoke", fake_invoke)
    with pytest.raises(RuntimeError, match="without an assistant reply"):
        asyncio.run(agent._opencode_run_turn("hi"))
