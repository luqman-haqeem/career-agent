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

    async def fake_send_doc(bot_, chat_id, path, reply_markup=None, **kw):
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

    async def fake_send_doc(bot_, chat_id, path, reply_markup=None, **kw):
        sent.append(path.name)

    monkeypatch.setattr(bot, "_send_doc_chat", fake_send_doc)
    monkeypatch.setattr(bot.render, "render_json_to_pdf",
                        lambda p: config.RESUMES_DIR / "x.pdf")
    asyncio.run(bot._deliver_changed_resumes(
        object(), CHAT, before={}, thread=a, claimed="acme.json"))
    assert "acme.json" in sent
    assert "globex.json" not in sent   # belongs to thread b


# --- Create resume / Skip buttons ------------------------------------------
def test_a_job_thread_offers_the_two_buttons():
    """Answering 'yes' by typing is what broke; offer the real choices instead."""
    key = threads.new_thread(CHAT, "acme.com")
    kb = bot._job_actions_for(CHAT, key)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels == ["📄 Create resume", "⏭ Skip"]
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == [f"th:cv:{key}", f"th:sk:{key}"]


def test_callback_data_fits_telegrams_limit():
    key = threads.new_thread(CHAT, "acme.com")
    for row in bot._job_actions_for(CHAT, key).inline_keyboard:
        for b in row:
            assert len(b.callback_data.encode()) <= 64


def test_the_main_conversation_gets_no_job_buttons():
    assert bot._job_actions_for(CHAT, threads.MAIN) is None
    assert bot._job_actions_for(CHAT, None) is None


def test_buttons_stop_once_the_job_has_a_resume():
    """After delivery the Critique button is the useful next step, not this."""
    key = threads.new_thread(CHAT, "acme.com")
    threads.set_resume(CHAT, key, "acme.json")
    assert bot._job_actions_for(CHAT, key) is None


def test_an_unknown_thread_gets_no_buttons():
    assert bot._job_actions_for(CHAT, "tGONE") is None


def test_buttons_ride_on_the_last_chunk_only(monkeypatch):
    """Buttons belong under the end of an answer, not inside it."""
    monkeypatch.setattr(bot.telegram_format, "chunk", lambda t: iter(["a", "b", "c"]))
    key = threads.new_thread(CHAT, "acme.com")
    kb = bot._job_actions_for(CHAT, key)

    seen = []

    class Bot:
        async def send_message(self, chat_id, text, parse_mode=None,
                               reply_parameters=None, reply_markup=None, **kw):
            seen.append(reply_markup)
            return types.SimpleNamespace(message_id=len(seen))

    asyncio.run(bot._send_chat(Bot(), CHAT, "long", thread=key, reply_markup=kb))
    assert seen[:-1] == [None, None]
    assert seen[-1] is kb


def test_skip_closes_the_thread_and_its_session(monkeypatch):
    key = threads.new_thread(CHAT, "acme.com")
    bot.save_session_id(CHAT, "ses_job", key)
    sent = []

    class Query:
        data = f"th:sk:{key}"
        async def answer(self, text=None, show_alert=False): sent.append(("answer", text))
        async def edit_message_reply_markup(self, reply_markup=None): sent.append(("markup", reply_markup))

    async def fake_send_message(chat_id, text, parse_mode=None, **kw):
        sent.append(("msg", text))

    ctx = types.SimpleNamespace(bot=types.SimpleNamespace(send_message=fake_send_message))
    upd = types.SimpleNamespace(callback_query=Query(),
                                effective_chat=types.SimpleNamespace(id=CHAT))
    asyncio.run(bot._on_thread_action(upd, ctx, f"th:sk:{key}"))

    assert not threads.exists(CHAT, key)
    assert threads.current(CHAT) == threads.MAIN
    assert bot.load_session_id(CHAT, key) is None
    assert ("markup", None) in sent          # button consumed
    assert any(k == "msg" and "acme.com" in v for k, v in sent)


def test_create_resume_runs_in_the_threads_own_session(monkeypatch):
    """The JD lives in that session; the prompt must not restate it from memory."""
    key = threads.new_thread(CHAT, "acme.com", url="https://x.io/7")
    bot.save_session_id(CHAT, "ses_job", key)
    seen = {}

    async def fake_run_turn(prompt, session_id, model=None, retry_prefix=None, files=None):
        seen["session"] = session_id
        seen["prompt"] = prompt
        seen["retry_prefix"] = retry_prefix
        return "Emphasized AWS. [[RESUME:acme.json]]", "ses_job"

    async def noop(*a, **k):
        return None

    async def fake_send_message(chat_id, text, **kw):
        return types.SimpleNamespace(message_id=9)

    monkeypatch.setattr(bot, "run_turn", fake_run_turn)
    monkeypatch.setattr(bot, "_keep_typing", noop)
    monkeypatch.setattr(bot, "_send_chat", noop)
    monkeypatch.setattr(bot, "_deliver_changed_resumes", noop)
    monkeypatch.setattr(config, "model_for", lambda task: None)

    ctx = types.SimpleNamespace(bot=types.SimpleNamespace(send_message=fake_send_message))
    asyncio.run(bot._generate_resume_in_thread(ctx, CHAT, key))

    assert seen["session"] == "ses_job"                 # not a fresh session
    assert "the job in this conversation" in seen["prompt"]
    assert "[[RESUME:" in seen["prompt"]                # marker instruction attached
    assert "https://x.io/7" in seen["retry_prefix"]     # recoverable if it fails


def test_tapping_a_button_on_a_closed_thread_is_graceful():
    answered = []

    class Query:
        async def answer(self, text=None, show_alert=False): answered.append(text)
        async def edit_message_reply_markup(self, reply_markup=None): pass

    upd = types.SimpleNamespace(callback_query=Query(),
                                effective_chat=types.SimpleNamespace(id=CHAT))
    asyncio.run(bot._on_thread_action(upd, types.SimpleNamespace(bot=None), "th:cv:tGONE"))
    assert answered and "closed" in answered[0].lower()


def test_malformed_thread_callback_is_ignored():
    answered = []

    class Query:
        async def answer(self, text=None, show_alert=False): answered.append(text)

    upd = types.SimpleNamespace(callback_query=Query(),
                                effective_chat=types.SimpleNamespace(id=CHAT))
    asyncio.run(bot._on_thread_action(upd, types.SimpleNamespace(bot=None), "th:cv"))
    assert answered == [None]


# --- sticky routing --------------------------------------------------------
def test_typing_continues_the_thread_you_are_in():
    """The bug: the bot asked a question, 'yes' was typed, and it went to main."""
    thread, _ = bot._route_thread(_update("https://acme.com/job/1", message_id=1))
    again, opened = bot._route_thread(_update("yes", message_id=2))
    assert again == thread
    assert opened is False


def test_a_new_link_switches_to_a_new_thread():
    a, _ = bot._route_thread(_update("https://a.com/job/1", message_id=1))
    b, opened = bot._route_thread(_update("https://b.com/job/2", message_id=2))
    assert b != a and opened is True
    assert bot._route_thread(_update("yes", message_id=3))[0] == b


def test_replying_switches_the_current_thread_back():
    a, _ = bot._route_thread(_update("https://a.com/job/1", message_id=1))
    threads.bind_message(CHAT, 500, a)
    b, _ = bot._route_thread(_update("https://b.com/job/2", message_id=2))
    assert threads.current(CHAT) == b
    back, _ = bot._route_thread(_update("shorter", 9, reply_to_id=500))
    assert back == a
    assert bot._route_thread(_update("and again", message_id=10))[0] == a


def test_typing_stays_in_main_when_no_thread_is_open():
    assert bot._route_thread(_update("what are my goals?"))[0] == threads.MAIN


def test_main_command_leaves_the_current_thread():
    key, _ = bot._route_thread(_update("https://acme.com/job/1", message_id=1))
    assert threads.current(CHAT) == key
    threads.set_current(CHAT, threads.MAIN)
    assert bot._route_thread(_update("hello", message_id=2))[0] == threads.MAIN


def test_forgetting_the_current_thread_falls_back_to_main():
    """A failed Apply drops its thread; typing must not point at a dead one."""
    key, _ = bot._route_thread(_update("https://acme.com/job/1", message_id=1))
    threads.forget(CHAT, key)
    assert threads.current(CHAT) == threads.MAIN


def test_uploads_are_sticky_too():
    key, _ = bot._route_thread(_update("https://acme.com/job/1", message_id=1))
    assert bot._route_thread(_update("see attached", message_id=2),
                             allow_new=False)[0] == key


def test_an_upload_never_opens_a_thread_from_a_link():
    upd = _update("https://acme.com/jd.pdf")
    thread, opened = bot._route_thread(upd, allow_new=False)
    assert thread == threads.MAIN and opened is False


# --- lost-session recovery -------------------------------------------------
def test_session_reset_is_detected():
    assert bot._session_was_reset("ses_old", "ses_new") is True
    assert bot._session_was_reset("ses_old", "ses_old") is False
    assert bot._session_was_reset(None, "ses_new") is False   # first turn, not a reset


def test_retry_prefix_carries_the_job_and_its_url():
    """A lost session must not come back asking for a JD already supplied."""
    key = threads.new_thread(CHAT, "linkedin.com", url="https://x.io/job/7")
    threads.set_label(CHAT, key, "Backend Software Engineer — Wurth IT")
    prefix = bot._retry_prefix(CHAT, key)
    assert "Backend Software Engineer — Wurth IT" in prefix
    assert "https://x.io/job/7" in prefix


def test_retry_prefix_is_none_for_the_main_conversation():
    assert bot._retry_prefix(CHAT, threads.MAIN) is None
    assert bot._retry_prefix(CHAT, None) is None


def test_retry_prefix_survives_a_thread_with_no_url():
    key = threads.new_thread(CHAT, "Backend Developer — Avanade", named=True)
    prefix = bot._retry_prefix(CHAT, key)
    assert "Avanade" in prefix
    assert "http" not in prefix


def test_lost_history_notice_names_the_job():
    key = threads.new_thread(CHAT, "linkedin.com")
    threads.set_label(CHAT, key, "Backend Software Engineer — Wurth IT")
    notice = bot._lost_history_notice(CHAT, key)
    assert "Wurth IT" in notice
    assert notice.endswith("\n\n")


# --- job marker (thread naming) --------------------------------------------
def test_job_marker_becomes_a_position_company_label():
    clean, label = bot.strip_job_marker(
        "Moderate fit.\n\n[[JOB:Backend Developer|Avanade]]")
    assert label == "Backend Developer — Avanade"
    assert clean == "Moderate fit."


def test_job_marker_without_a_company_still_names_the_position():
    _clean, label = bot.strip_job_marker("ok [[JOB:Backend Developer|]]")
    assert label == "Backend Developer"


def test_job_marker_without_a_pipe_is_used_whole():
    _clean, label = bot.strip_job_marker("ok [[JOB:Backend Developer]]")
    assert label == "Backend Developer"


def test_empty_job_marker_is_stripped_but_names_nothing():
    clean, label = bot.strip_job_marker("ok [[JOB:]]")
    assert label is None
    assert "[[JOB" not in clean


def test_missing_job_marker_leaves_text_alone():
    clean, label = bot.strip_job_marker("just a chat message")
    assert label is None
    assert clean == "just a chat message"


def test_a_named_thread_shows_position_company_in_its_header():
    key = threads.new_thread(CHAT, "linkedin.com")
    threads.set_label(CHAT, key, "Backend Developer — Avanade")
    assert bot._thread_prefix(CHAT, key) == "🧵 **Backend Developer — Avanade**\n\n"


# --- visible threading: label + reply quoting ------------------------------
class _RecordingBot:
    """Captures send_message calls, including how each one was anchored."""

    def __init__(self):
        self.calls = []
        self._next_id = 100

    async def send_message(self, chat_id, text, parse_mode=None,
                           reply_parameters=None, **kw):
        self._next_id += 1
        self.calls.append({"text": text, "reply_parameters": reply_parameters})
        return types.SimpleNamespace(message_id=self._next_id)


def test_thread_prefix_labels_a_job_thread():
    key = threads.new_thread(CHAT, "boards.briohr.com")
    assert bot._thread_prefix(CHAT, key) == "🧵 **boards.briohr.com**\n\n"


def test_thread_prefix_is_empty_for_the_main_conversation():
    assert bot._thread_prefix(CHAT, threads.MAIN) == ""
    assert bot._thread_prefix(CHAT, None) == ""


def test_thread_prefix_strips_markdown_from_the_label():
    """A company name with '*' would otherwise eat the surrounding formatting."""
    key = threads.new_thread(CHAT, "A*C_ME `Ltd`")
    assert bot._thread_prefix(CHAT, key) == "🧵 **ACME Ltd**\n\n"


def test_reply_params_are_tolerant_of_a_deleted_target():
    p = bot._reply_params(77)
    assert p.message_id == 77
    assert p.allow_sending_without_reply is True
    assert bot._reply_params(None) is None


def test_sent_text_carries_the_thread_label():
    key = threads.new_thread(CHAT, "acme.com")
    fake = _RecordingBot()
    asyncio.run(bot._send_chat(fake, CHAT, "Moderate fit.", thread=key))
    assert "acme.com" in fake.calls[0]["text"]


def test_main_conversation_gets_no_label():
    fake = _RecordingBot()
    asyncio.run(bot._send_chat(fake, CHAT, "Saved to memory.", thread=threads.MAIN))
    assert "🧵" not in fake.calls[0]["text"]


def test_the_reply_quotes_the_message_it_answers():
    fake = _RecordingBot()
    asyncio.run(bot._send_chat(fake, CHAT, "answer", reply_to=55))
    assert fake.calls[0]["reply_parameters"].message_id == 55


def test_only_the_first_chunk_quotes(monkeypatch):
    """A long answer must not repeat the quote bar on every chunk."""
    monkeypatch.setattr(bot.telegram_format, "chunk", lambda t: iter(["a", "b", "c"]))
    fake = _RecordingBot()
    asyncio.run(bot._send_chat(fake, CHAT, "long", reply_to=55))
    assert fake.calls[0]["reply_parameters"] is not None
    assert [c["reply_parameters"] for c in fake.calls[1:]] == [None, None]


def test_every_sent_message_is_bound_to_its_thread():
    """Replying to any part of a multi-chunk answer must route back."""
    key = threads.new_thread(CHAT, "acme.com")
    fake = _RecordingBot()
    ids = asyncio.run(bot._send_chat(fake, CHAT, "answer", thread=key))
    assert ids
    for mid in ids:
        assert threads.thread_for_message(CHAT, mid) == key


def test_apply_names_its_thread_position_first(monkeypatch):
    """Guards against swapping title/company when building the Apply label."""
    async def fake_run_turn(prompt, session_id, model=None):
        return "done", "ses-new"

    async def noop(*a, **k):
        return None

    async def fake_send_message(chat_id, text, **kw):
        return types.SimpleNamespace(message_id=77)

    monkeypatch.setattr(bot, "run_turn", fake_run_turn)
    monkeypatch.setattr(bot, "_keep_typing", noop)
    monkeypatch.setattr(bot, "_send_chat", noop)
    monkeypatch.setattr(bot, "_deliver_changed_resumes", noop)
    monkeypatch.setattr(bot.jobs_store, "set_decision", lambda *a, **k: None)
    monkeypatch.setattr(config, "model_for", lambda task: None)

    ctx = types.SimpleNamespace(bot=types.SimpleNamespace(send_message=fake_send_message))
    job = {"title": "Backend Developer", "company": "Avanade",
           "location": "KL", "url": "https://x.io/1"}
    asyncio.run(bot._generate_resume_for(ctx, CHAT, job, "jid123"))

    labels = [meta["label"] for _k, meta in threads.listing(CHAT)]
    assert labels == ["Backend Developer — Avanade"]


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

    async def fake_send_chat(bot_, chat_id, text, thread=None, **kw):
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
