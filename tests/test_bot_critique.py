import asyncio

import pytest

import bot
import config


class _FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answered = None
        self.markup_edits = []

    async def answer(self, text=None, show_alert=False):
        self.answered = (text, show_alert)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_edits.append(reply_markup)


class _FakeUpdate:
    def __init__(self, query, chat_id=123):
        self.callback_query = query
        class _Chat:
            id = chat_id
        self.effective_chat = _Chat()


class _FakeCtx:
    class bot:  # noqa: N801 - mimic telegram ctx.bot namespace
        sent = []

        @staticmethod
        async def send_message(chat_id, text):
            _FakeCtx.bot.sent.append((chat_id, text))


@pytest.fixture(autouse=True)
def reset_critique_state():
    saved_tokens = dict(bot._critique_tokens)
    yield
    bot._critique_tokens.clear()
    bot._critique_tokens.update(saved_tokens)


def test_register_critique_roundtrips_and_is_unique():
    t1 = bot._register_critique("everest-engineering-senior-full-stack.json")
    t2 = bot._register_critique("acme-backend.json")
    assert t1 != t2
    assert bot._critique_tokens[t1] == "everest-engineering-senior-full-stack.json"
    assert bot._critique_tokens[t2] == "acme-backend.json"


def test_critique_keyboard_callback_data_format_and_length():
    token = bot._register_critique(
        "a-very-long-company-name-senior-staff-full-stack-platform-engineer.json")
    kb = bot._critique_keyboard(token)
    btn = kb.inline_keyboard[0][0]
    assert btn.text == "📝 Critique it"
    assert btn.callback_data == f"crit:{token}"
    # Telegram hard limit is 64 bytes — token map keeps us well under it.
    assert len(btn.callback_data.encode()) <= 64


def test_delivery_attaches_button_to_pdf_not_json(monkeypatch):
    sent = []  # (filename, reply_markup)

    async def fake_send_doc(bot_, chat_id, path, reply_markup=None):
        sent.append((path.name, reply_markup))

    monkeypatch.setattr(bot, "_send_doc_chat", fake_send_doc)
    # render returns a sibling .pdf path; file need not exist (send is faked).
    monkeypatch.setattr(bot.render, "render_json_to_pdf",
                        lambda p: p.with_suffix(".pdf"))
    # Pretend exactly one resume changed since `before`.
    monkeypatch.setattr(bot, "_resume_snapshot", lambda: {"acme-backend.json": 2.0})

    asyncio.run(bot._deliver_changed_resumes(object(), 123, before={}))

    names = [n for n, _ in sent]
    assert names == ["acme-backend.pdf", "acme-backend.json"]
    pdf_markup = sent[0][1]
    json_markup = sent[1][1]
    # PDF carries the Critique-it button; JSON does not (button shown once).
    assert pdf_markup is not None
    assert pdf_markup.inline_keyboard[0][0].callback_data.startswith("crit:")
    assert json_markup is None


def test_delivery_falls_back_to_json_button_when_pdf_render_fails(monkeypatch):
    sent = []

    async def fake_send_doc(bot_, chat_id, path, reply_markup=None):
        sent.append((path.name, reply_markup))

    def boom(p):
        raise RuntimeError("tectonic exploded")

    async def fake_send_msg(chat_id, text):
        pass

    class FakeBot:
        send_message = staticmethod(fake_send_msg)

    monkeypatch.setattr(bot, "_send_doc_chat", fake_send_doc)
    monkeypatch.setattr(bot.render, "render_json_to_pdf", boom)
    monkeypatch.setattr(bot, "_resume_snapshot", lambda: {"acme-backend.json": 2.0})

    asyncio.run(bot._deliver_changed_resumes(FakeBot(), 123, before={}))

    # Only the JSON was sent, and it now carries the button (PDF never made it).
    assert [n for n, _ in sent] == ["acme-backend.json"]
    assert sent[0][1] is not None
    assert sent[0][1].inline_keyboard[0][0].callback_data.startswith("crit:")


def test_critique_tap_runs_on_critique_model(monkeypatch):
    _FakeCtx.bot.sent = []
    bot._critique_tokens["c42"] = "acme-backend.json"

    captured = {}

    async def fake_run_turn(prompt, session_id, model=None):
        captured["prompt"] = prompt
        captured["model"] = model
        return "📄 acme — 82/100", "sess-1"

    sent_text = {}

    async def fake_send_chat(bot_, chat_id, text):
        sent_text["text"] = text

    async def fake_keep_typing(bot_, chat_id):
        return

    monkeypatch.setattr(bot, "run_turn", fake_run_turn)
    monkeypatch.setattr(bot, "_send_chat", fake_send_chat)
    monkeypatch.setattr(bot, "_keep_typing", fake_keep_typing)
    monkeypatch.setattr(bot, "load_session_id", lambda c: "sess-0")
    monkeypatch.setattr(bot, "save_session_id", lambda c, s: None)
    monkeypatch.setattr(config, "model_for", lambda task: f"model-{task}")

    q = _FakeQuery("crit:c42")
    asyncio.run(bot._on_critique_action(_FakeUpdate(q), _FakeCtx, "crit:c42"))

    assert captured["model"] == "model-critique"
    assert "resumes/acme-backend.json" in captured["prompt"]
    assert q.markup_edits == [None]          # button stripped exactly once
    assert sent_text["text"] == "📄 acme — 82/100"


def test_critique_tap_with_stale_token_is_graceful(monkeypatch):
    async def fake_run_turn(*a, **k):
        raise AssertionError("must not run a turn for a stale token")

    monkeypatch.setattr(bot, "run_turn", fake_run_turn)
    q = _FakeQuery("crit:gone")
    asyncio.run(bot._on_critique_action(_FakeUpdate(q), _FakeCtx, "crit:gone"))

    assert q.answered[0].startswith("Tap expired")
    assert q.answered[1] is True   # show_alert=True on the 'tap expired' modal
    assert q.markup_edits == [None]          # button still removed


def test_old_token_misses_after_restart_simulation():
    # Resume A registered before a "restart".
    tok_a = bot._register_critique("resume-A.json")
    # Simulate a process restart: the in-memory map is cleared.
    bot._critique_tokens.clear()
    # Resume B registered after restart must NOT reuse A's token.
    tok_b = bot._register_critique("resume-B.json")
    assert tok_b != tok_a                      # random tokens don't collide
    assert tok_a not in bot._critique_tokens   # A's old button now misses -> graceful path
