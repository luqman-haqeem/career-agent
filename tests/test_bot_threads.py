"""Thread routing, session isolation, and per-thread resume delivery."""
import asyncio
import types

import pytest

import bot
import config
import threads

CHAT = 4242


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point sessions + the thread store at a temp dir for every test here."""
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "RESUMES_DIR", tmp_path / "resumes")
    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "resumes").mkdir(parents=True, exist_ok=True)
    bot._thread_locks.clear()
    monkeypatch.setattr(bot.onboarding, "status", lambda: "done")


def _msg(text="hi", message_id=1, reply_to_id=None):
    reply_to = types.SimpleNamespace(message_id=reply_to_id) if reply_to_id else None
    return types.SimpleNamespace(text=text, message_id=message_id,
                                 reply_to_message=reply_to)


def _update(text="hi", message_id=1, reply_to_id=None):
    return types.SimpleNamespace(
        message=_msg(text, message_id, reply_to_id),
        effective_chat=types.SimpleNamespace(id=CHAT))


# --- session isolation -----------------------------------------------------
def test_main_thread_keeps_the_pre_thread_session_path():
    """An existing install must not lose its conversation to this upgrade."""
    assert bot._session_path(CHAT).name == f"{CHAT}.json"
    assert bot._session_path(CHAT, threads.MAIN).name == f"{CHAT}.json"


def test_each_thread_gets_its_own_session_file():
    a = bot._session_path(CHAT, "tAAA")
    b = bot._session_path(CHAT, "tBBB")
    assert a != b != bot._session_path(CHAT, threads.MAIN)


def test_sessions_do_not_leak_between_threads():
    bot.save_session_id(CHAT, "ses_main", threads.MAIN)
    bot.save_session_id(CHAT, "ses_a", "tAAA")
    bot.save_session_id(CHAT, "ses_b", "tBBB")
    assert bot.load_session_id(CHAT, threads.MAIN) == "ses_main"
    assert bot.load_session_id(CHAT, "tAAA") == "ses_a"
    assert bot.load_session_id(CHAT, "tBBB") == "ses_b"


def test_unknown_thread_has_no_session():
    assert bot.load_session_id(CHAT, "tNOPE") is None


def test_clear_all_sessions_closes_every_thread():
    key = threads.new_thread(CHAT, "acme.com")
    bot.save_session_id(CHAT, "ses_main", threads.MAIN)
    bot.save_session_id(CHAT, "ses_job", key)
    bot._clear_all_sessions(CHAT)
    assert bot.load_session_id(CHAT, threads.MAIN) is None
    assert bot.load_session_id(CHAT, key) is None
    assert threads.listing(CHAT) == []


# --- routing ---------------------------------------------------------------
def test_a_plain_message_stays_in_the_main_thread():
    thread, opened = bot._route_thread(_update("what are my goals?"))
    assert thread == threads.MAIN
    assert opened is False


def test_a_link_opens_a_new_thread():
    thread, opened = bot._route_thread(_update("https://jobstreet.com/job/1"))
    assert opened is True
    assert thread != threads.MAIN
    assert threads.get(CHAT, thread)["label"] == "jobstreet.com"


def test_two_links_open_two_independent_threads():
    """The actual complaint: several links in one chat used to share a history."""
    a, _ = bot._route_thread(_update("https://a.com/job/1", message_id=1))
    b, _ = bot._route_thread(_update("https://b.com/job/2", message_id=2))
    assert a != b


def test_replying_to_a_bound_message_returns_to_its_thread():
    key = threads.new_thread(CHAT, "acme.com")
    threads.bind_message(CHAT, 500, key)
    thread, opened = bot._route_thread(_update("make it shorter", 9, reply_to_id=500))
    assert thread == key
    assert opened is False


def test_replying_with_a_link_stays_in_the_thread_it_replied_to():
    """A follow-up link inside a thread is context for that job, not a new one."""
    key = threads.new_thread(CHAT, "acme.com")
    threads.bind_message(CHAT, 500, key)
    thread, opened = bot._route_thread(
        _update("also see https://acme.com/team", 9, reply_to_id=500))
    assert thread == key
    assert opened is False


def test_replying_to_an_unbound_message_falls_back():
    thread, _ = bot._route_thread(_update("hello", 9, reply_to_id=999))
    assert thread == threads.MAIN


def test_replying_to_a_forgotten_thread_falls_back_to_main():
    """A /reset between the message and the reply must not route into a dead thread."""
    key = threads.new_thread(CHAT, "acme.com")
    threads.bind_message(CHAT, 500, key)
    threads.forget(CHAT, key)
    thread, _ = bot._route_thread(_update("still there?", 9, reply_to_id=500))
    assert thread == threads.MAIN


def test_onboarding_never_forks_into_a_thread(monkeypatch):
    """Onboarding is one linear interview; a pasted link must not split it."""
    monkeypatch.setattr(bot.onboarding, "status", lambda: "in_progress")
    thread, opened = bot._route_thread(_update("my github is https://github.com/x"))
    assert thread == threads.MAIN
    assert opened is False


def test_uploads_never_open_a_thread():
    """Uploading a CV is a memory operation; it belongs in the main conversation."""
    upd = _update("https://acme.com/jd.pdf")
    thread, opened = bot._route_thread(upd, allow_new=False)
    assert thread == threads.MAIN
    assert opened is False


def test_an_upload_replying_into_a_thread_stays_there():
    """Sending the JD as a PDF inside a job thread must not fall back to main."""
    key = threads.new_thread(CHAT, "acme.com")
    threads.bind_message(CHAT, 500, key)
    thread, _ = bot._route_thread(_update("here it is", 9, reply_to_id=500),
                                  allow_new=False)
    assert thread == key


def test_routing_survives_a_message_with_no_text_attribute():
    """Photo/document messages carry .caption, not .text — neither is guaranteed."""
    msg = types.SimpleNamespace(message_id=3, reply_to_message=None)  # no .text
    upd = types.SimpleNamespace(message=msg,
                                effective_chat=types.SimpleNamespace(id=CHAT))
    assert bot._route_thread(upd, allow_new=False)[0] == threads.MAIN


def test_a_caption_with_a_link_can_open_a_thread():
    msg = types.SimpleNamespace(message_id=3, reply_to_message=None,
                                caption="https://acme.com/job/7")
    upd = types.SimpleNamespace(message=msg,
                                effective_chat=types.SimpleNamespace(id=CHAT))
    thread, opened = bot._route_thread(upd)
    assert opened is True
    assert threads.get(CHAT, thread)["label"] == "acme.com"


# --- resume marker ---------------------------------------------------------
def test_marker_is_extracted_and_stripped():
    clean, name = bot.strip_resume_marker(
        "Emphasized your AWS work.\n\n[[RESUME:avanade-backend.json]]")
    assert name == "avanade-backend.json"
    assert "[[RESUME" not in clean
    assert clean == "Emphasized your AWS work."


def test_missing_marker_returns_no_claim():
    clean, name = bot.strip_resume_marker("Just answering a question.")
    assert name is None
    assert clean == "Just answering a question."


def test_marker_path_is_reduced_to_a_bare_filename():
    """The agent must not be able to steer delivery outside resumes/."""
    _clean, name = bot.strip_resume_marker("done [[RESUME:../../etc/passwd]]")
    assert name == "passwd"


def test_empty_marker_is_ignored():
    clean, name = bot.strip_resume_marker("done [[RESUME: ]]")
    assert name is None
    assert "[[RESUME" in clean  # left alone rather than half-stripped


def test_marker_survives_surrounding_text():
    clean, name = bot.strip_resume_marker("a\n[[RESUME:x.json]]\nb")
    assert name == "x.json"
    assert "x.json" not in clean


# --- delivery isolation ----------------------------------------------------
def test_a_threads_own_resume_is_deliverable():
    key = threads.new_thread(CHAT, "acme.com")
    threads.set_resume(CHAT, key, "acme.json")
    assert bot._deliverable_here(CHAT, "acme.json", key, None) is True


def test_another_threads_resume_is_never_delivered_here():
    """The cross-delivery bug: two jobs in flight, each must keep its own PDF."""
    a = threads.new_thread(CHAT, "a.com")
    b = threads.new_thread(CHAT, "b.com")
    threads.set_resume(CHAT, a, "acme.json")
    threads.set_resume(CHAT, b, "globex.json")
    assert bot._deliverable_here(CHAT, "globex.json", a, None) is False
    assert bot._deliverable_here(CHAT, "acme.json", b, None) is False


def test_an_unowned_resume_still_gets_delivered():
    """A turn that forgets the marker degrades to the old behaviour, not silence."""
    key = threads.new_thread(CHAT, "a.com")
    assert bot._deliverable_here(CHAT, "orphan.json", key, None) is True


def test_a_freshly_claimed_file_is_deliverable_even_if_owned_elsewhere():
    other = threads.new_thread(CHAT, "b.com")
    threads.set_resume(CHAT, other, "shared.json")
    mine = threads.new_thread(CHAT, "a.com")
    assert bot._deliverable_here(CHAT, "shared.json", mine, "shared.json") is True


def test_delivery_claims_ownership_for_the_running_thread(monkeypatch):
    key = threads.new_thread(CHAT, "acme.com")
    (config.RESUMES_DIR / "acme.json").write_text("{}", encoding="utf-8")
    sent = []

    async def fake_send_doc(bot_, chat_id, path, reply_markup=None):
        sent.append(path.name)

    monkeypatch.setattr(bot, "_send_doc_chat", fake_send_doc)
    monkeypatch.setattr(bot.render, "render_json_to_pdf",
                        lambda p: config.RESUMES_DIR / "acme.pdf")
    asyncio.run(bot._deliver_changed_resumes(
        object(), CHAT, before={}, thread=key, claimed="acme.json"))
    assert threads.resume_owner(CHAT, "acme.json") == key
    assert "acme.json" in sent


def test_delivery_skips_a_file_owned_by_another_thread(monkeypatch):
    a = threads.new_thread(CHAT, "a.com")
    b = threads.new_thread(CHAT, "b.com")
    threads.set_resume(CHAT, b, "globex.json")
    for name in ("acme.json", "globex.json"):
        (config.RESUMES_DIR / name).write_text("{}", encoding="utf-8")
    sent = []

    async def fake_send_doc(bot_, chat_id, path, reply_markup=None):
        sent.append(path.name)

    monkeypatch.setattr(bot, "_send_doc_chat", fake_send_doc)
    monkeypatch.setattr(bot.render, "render_json_to_pdf",
                        lambda p: config.RESUMES_DIR / "x.pdf")
    asyncio.run(bot._deliver_changed_resumes(
        object(), CHAT, before={}, thread=a, claimed="acme.json"))
    assert "acme.json" in sent
    assert "globex.json" not in sent   # belongs to thread b


# --- critique routing ------------------------------------------------------
class _FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        pass


def _critique_update(query):
    return types.SimpleNamespace(callback_query=query,
                                 effective_chat=types.SimpleNamespace(id=CHAT))


def _run_critique(monkeypatch, name, seen):
    async def fake_run_turn(prompt, session_id, model=None):
        seen["session"] = session_id
        return "scorecard", "ses-new"

    async def noop_typing(bot_, chat_id):
        return

    async def fake_send_chat(bot_, chat_id, text, thread=None):
        seen["thread"] = thread

    monkeypatch.setattr(bot, "run_turn", fake_run_turn)
    monkeypatch.setattr(bot, "_keep_typing", noop_typing)
    monkeypatch.setattr(bot, "_send_chat", fake_send_chat)
    monkeypatch.setattr(config, "model_for", lambda task: None)
    bot._critique_tokens["tok"] = name
    ctx = types.SimpleNamespace(bot=object())
    asyncio.run(bot._on_critique_action(_critique_update(_FakeQuery("crit:tok")),
                                        ctx, "crit:tok"))


def test_critique_runs_in_the_thread_that_built_the_resume(monkeypatch):
    """'the JD from this conversation' is only true in that job's own session."""
    key = threads.new_thread(CHAT, "acme.com")
    threads.set_resume(CHAT, key, "acme.json")
    bot.save_session_id(CHAT, "ses_job", key)
    bot.save_session_id(CHAT, "ses_main", threads.MAIN)
    seen = {}
    _run_critique(monkeypatch, "acme.json", seen)
    assert seen["session"] == "ses_job"
    assert seen["thread"] == key


def test_critique_of_a_pre_threads_resume_falls_back_to_main(monkeypatch):
    bot.save_session_id(CHAT, "ses_main", threads.MAIN)
    seen = {}
    _run_critique(monkeypatch, "legacy.json", seen)
    assert seen["session"] == "ses_main"
    assert seen["thread"] == threads.MAIN


# --- locking ---------------------------------------------------------------
def test_same_thread_shares_one_lock():
    assert bot._lock_for(CHAT, "tAAA") is bot._lock_for(CHAT, "tAAA")


def test_different_threads_get_different_locks():
    """Serialize within a job, run freely across jobs."""
    assert bot._lock_for(CHAT, "tAAA") is not bot._lock_for(CHAT, "tBBB")
    assert bot._lock_for(CHAT, "tAAA") is not bot._lock_for(9999, "tAAA")


def test_the_lock_actually_serializes_same_thread_turns():
    """Two turns on one session must not interleave: that is what corrupts it."""
    order = []

    async def turn(tag):
        async with bot._lock_for(CHAT, "tAAA"):
            order.append(f"{tag}-start")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-end")

    async def main():
        await asyncio.gather(turn("a"), turn("b"))

    asyncio.run(main())
    assert order in (["a-start", "a-end", "b-start", "b-end"],
                     ["b-start", "b-end", "a-start", "a-end"])
