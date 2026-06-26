import json

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
