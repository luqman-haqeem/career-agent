import asyncio
import config

import pytest

import bot


@pytest.fixture(autouse=True)
def reset_critique_state():
    saved_tokens = dict(bot._critique_tokens)
    saved_seq = bot._critique_seq
    yield
    bot._critique_tokens.clear()
    bot._critique_tokens.update(saved_tokens)
    bot._critique_seq = saved_seq


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
