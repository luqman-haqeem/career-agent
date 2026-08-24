"""Auto-drafting the top scan match's resume.

The scan surfaces matches at 09:00; tapping Apply then costs a minute of
waiting. Drafting the best one in advance makes the button instant.

The load-bearing constraint: jobs_store.decisions() feeds
preferences.run_synthesis(), which writes memory/preferences.md, which
re-ranks the NEXT scan. A draft is not a decision — if drafting recorded
"applied", the scanner would train on its own output.
"""
import asyncio
import json

import pytest

import bot
import config
import jobs_store

JOB_TOP = {"id": "j-top", "title": "AI Full Stack Engineer", "company": "Lenovo",
           "location": "KL", "url": "https://x/1", "fit_score": 9}
JOB_MID = {"id": "j-mid", "title": "Backend Developer", "company": "Ocean Hub",
           "location": "KL", "url": "https://x/2", "fit_score": 8}
JOB_LOW = {"id": "j-low", "title": "Support Engineer", "company": "Acme",
           "location": "KL", "url": "https://x/3", "fit_score": 5}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS_STORE", tmp_path / "jobs.json")
    # record() derives the id from url/title/company and THAT is the store key —
    # the literal ids below are never what gets written. scan.run_scan() writes
    # the derived id back onto each match, so mirror it here.
    for j in (JOB_TOP, JOB_MID, JOB_LOW):
        j["id"] = jobs_store.record(j, "offered")
    return tmp_path


@pytest.fixture
def resumes(tmp_path, monkeypatch):
    d = tmp_path / "resumes"
    d.mkdir()
    monkeypatch.setattr(config, "RESUMES_DIR", d)
    return d


class _Bot:
    def __init__(self):
        self.sent = []
        self.docs = []
        self.markups = []
        self.edits = []
        self._id = 100

    async def send_message(self, chat_id=None, text=None, **kw):
        self._id += 1
        self.sent.append(text)
        return type("M", (), {"message_id": self._id})()

    async def send_document(self, chat_id=None, document=None, **kw):
        self.docs.append(document)
        self.markups.append(kw.get("reply_markup"))

    async def send_chat_action(self, *a, **kw):
        pass

    async def edit_message_reply_markup(self, chat_id=None, message_id=None,
                                        reply_markup=None):
        self.edits.append((message_id, reply_markup))


class _Ctx:
    def __init__(self):
        self.bot = _Bot()


# --- the decision-neutrality guard -----------------------------------------

def test_drafting_records_no_decision(store, resumes, monkeypatch):
    """The whole reason this is not just a call to _generate_resume_for.

    decisions() is the synthesis input for memory/preferences.md. A drafted
    job the user never chose must not appear there.
    """
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json"))
    asyncio.run(bot._auto_draft_top(_Ctx(), 1, [JOB_TOP, JOB_MID], {}))
    assert jobs_store.get(JOB_TOP["id"])["state"] == "offered"
    assert jobs_store.decisions() == []


def test_tapping_apply_does_record_the_decision(store, resumes, monkeypatch):
    """A tap IS a real preference signal and must still be learned from."""
    _seed_draft(resumes, "cv.json")
    ctx = _Ctx()
    asyncio.run(bot._deliver_stored_draft(ctx, 1, jobs_store.get(JOB_TOP["id"]), JOB_TOP["id"]))
    assert jobs_store.get(JOB_TOP["id"])["state"] == "applied"


# --- choosing the match ----------------------------------------------------

def test_the_highest_scoring_match_is_chosen(store, resumes, monkeypatch):
    drafted = []
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json", drafted))
    asyncio.run(bot._auto_draft_top(_Ctx(), 1, [JOB_LOW, JOB_TOP, JOB_MID], {}))
    assert [j["id"] for j in drafted] == [JOB_TOP["id"]]


def test_only_one_resume_is_drafted_per_scan(store, resumes, monkeypatch):
    drafted = []
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json", drafted))
    asyncio.run(bot._auto_draft_top(_Ctx(), 1, [JOB_LOW, JOB_TOP, JOB_MID], {}))
    assert len(drafted) == 1


def test_a_missing_fit_score_does_not_crash_the_pick(store, resumes, monkeypatch):
    drafted = []
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json", drafted))
    unscored = {"id": "j-none", "title": "X", "company": "Y", "url": "u"}
    asyncio.run(bot._auto_draft_top(_Ctx(), 1, [unscored, JOB_TOP], {}))
    assert drafted[0]["id"] == JOB_TOP["id"]


def test_nothing_happens_with_no_matches(store, resumes, monkeypatch):
    drafted = []
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json", drafted))
    asyncio.run(bot._auto_draft_top(_Ctx(), 1, [], {}))
    assert drafted == []


def test_the_feature_can_be_switched_off(store, resumes, monkeypatch):
    monkeypatch.setattr(config, "AUTO_DRAFT_TOP_MATCH", False)
    drafted = []
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json", drafted))
    asyncio.run(bot._auto_draft_top(_Ctx(), 1, [JOB_TOP], {}))
    assert drafted == []


# --- persistence -----------------------------------------------------------

def test_the_draft_is_persisted_to_disk_not_memory(store, resumes, monkeypatch):
    """The 09:00 scan and the tap are hours and possibly a restart apart."""
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json"))
    asyncio.run(bot._auto_draft_top(_Ctx(), 1, [JOB_TOP], {}))
    raw = json.loads((config.JOBS_STORE).read_text())
    assert raw["jobs"][JOB_TOP["id"]]["resume_file"] == "cv.json"
    assert raw["jobs"][JOB_TOP["id"]]["resume_note"]


# --- delivery on tap -------------------------------------------------------

def test_a_stored_draft_is_delivered_without_a_model_call(store, resumes, monkeypatch):
    _seed_draft(resumes, "cv.json")

    async def boom(*a, **k):
        raise AssertionError("a stored draft must not re-run the model")

    monkeypatch.setattr(bot, "run_turn", boom)
    ctx = _Ctx()
    asyncio.run(bot._deliver_stored_draft(ctx, 1, jobs_store.get(JOB_TOP["id"]), JOB_TOP["id"]))
    assert ctx.bot.docs, "the PDF/JSON should have been sent"


def test_a_stored_draft_is_delivered_as_a_rendered_pdf(store, resumes, monkeypatch):
    """Not the raw .json — the tap must give the same artifact Apply does."""
    _seed_draft(resumes, "cv.json")
    pdf = resumes / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(bot.render, "render_json_to_pdf", lambda p: pdf)
    ctx = _Ctx()
    asyncio.run(bot._deliver_stored_draft(ctx, 1, jobs_store.get(JOB_TOP["id"]), JOB_TOP["id"]))
    names = [getattr(d, "name", str(d)) for d in ctx.bot.docs]
    # PDF first, then the portable .json — same pair the Apply path sends.
    assert any(str(n).endswith(".pdf") for n in names), names


def test_a_stored_draft_carries_the_critique_button(store, resumes, monkeypatch):
    """The one-tap critique offer must not be lost on the pre-drafted path."""
    _seed_draft(resumes, "cv.json")
    pdf = resumes / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(bot.render, "render_json_to_pdf", lambda p: pdf)
    ctx = _Ctx()
    asyncio.run(bot._deliver_stored_draft(ctx, 1, jobs_store.get(JOB_TOP["id"]), JOB_TOP["id"]))
    assert any(m is not None for m in ctx.bot.markups), "no keyboard went out with the PDF"


def test_the_agents_note_is_sent_with_the_stored_draft(store, resumes):
    _seed_draft(resumes, "cv.json", note="Emphasised your Python work; gap: no k8s.")
    ctx = _Ctx()
    asyncio.run(bot._deliver_stored_draft(ctx, 1, jobs_store.get(JOB_TOP["id"]), JOB_TOP["id"]))
    assert any("Emphasised your Python work" in t for t in ctx.bot.sent)


def test_a_deleted_resume_file_falls_back_instead_of_erroring(store, resumes):
    """The user may have cleaned out resumes/ between the scan and the tap."""
    _seed_draft(resumes, "cv.json")
    (resumes / "cv.json").unlink()
    ok = asyncio.run(bot._deliver_stored_draft(_Ctx(), 1, jobs_store.get(JOB_TOP["id"]), JOB_TOP["id"]))
    assert ok is False
    assert jobs_store.get(JOB_TOP["id"])["state"] == "offered", "no decision on a failed delivery"


def test_a_job_with_no_stored_draft_reports_that(store, resumes):
    ok = asyncio.run(bot._deliver_stored_draft(_Ctx(), 1, jobs_store.get(JOB_MID["id"]), JOB_MID["id"]))
    assert ok is False


# --- failure isolation -----------------------------------------------------

def test_a_failed_draft_leaves_the_scan_untouched(store, resumes, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("opencode fell over")

    monkeypatch.setattr(bot, "_draft_resume", boom)
    asyncio.run(bot._auto_draft_top(_Ctx(), 1, [JOB_TOP], {}))  # must not raise
    assert "resume_file" not in jobs_store.get(JOB_TOP["id"])


def test_a_failed_draft_sends_the_user_nothing(store, resumes, monkeypatch):
    """You should never learn that a background draft was even attempted."""
    async def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(bot, "_draft_resume", boom)
    ctx = _Ctx()
    asyncio.run(bot._auto_draft_top(ctx, 1, [JOB_TOP], {}))
    assert ctx.bot.sent == []
    assert ctx.bot.docs == []


def test_a_successful_draft_also_sends_the_user_nothing(store, resumes, monkeypatch):
    """'Draft it quietly' — the PDF waits for the tap."""
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json"))
    ctx = _Ctx()
    asyncio.run(bot._auto_draft_top(ctx, 1, [JOB_TOP], {}))
    assert ctx.bot.docs == []


# --- the button ------------------------------------------------------------

def test_the_card_button_is_relabelled_once_the_draft_is_ready(store, resumes, monkeypatch):
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json"))
    ctx = _Ctx()
    asyncio.run(bot._auto_draft_top(ctx, 1, [JOB_TOP], {JOB_TOP["id"]: 555}))
    assert ctx.bot.edits, "the card's keyboard should have been edited"
    msg_id, markup = ctx.bot.edits[0]
    assert msg_id == 555
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("ready" in t.lower() for t in labels)


def test_the_button_still_carries_the_apply_callback(store, resumes, monkeypatch):
    """Same callback: the tap must still record 'applied' and learn from it."""
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json"))
    ctx = _Ctx()
    asyncio.run(bot._auto_draft_top(ctx, 1, [JOB_TOP], {JOB_TOP["id"]: 555}))
    _, markup = ctx.bot.edits[0]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"job:apply:{JOB_TOP['id']}" in datas
    assert any(d.startswith("job:skip:") for d in datas)


def test_a_card_whose_id_was_not_captured_is_skipped_quietly(store, resumes, monkeypatch):
    monkeypatch.setattr(bot, "_draft_resume", _fake_draft("cv.json"))
    ctx = _Ctx()
    asyncio.run(bot._auto_draft_top(ctx, 1, [JOB_TOP], {}))   # no message id
    assert ctx.bot.edits == []
    assert jobs_store.get(JOB_TOP["id"])["resume_file"] == "cv.json"  # draft still stored


def test_send_job_card_returns_its_message_id(store, resumes):
    """_do_scan needs the id to relabel the button later."""
    ctx = _Ctx()
    mid = asyncio.run(bot._send_job_card(ctx.bot, 1, JOB_TOP))
    assert isinstance(mid, int)


# --- helpers ---------------------------------------------------------------

def _fake_draft(filename, collect=None):
    async def draft(ctx, chat_id, job, jid, **kw):
        if collect is not None:
            collect.append(job)
        (config.RESUMES_DIR / filename).write_text("{}", encoding="utf-8")
        return {"thread": "t1", "note": "Drafted.", "file": filename}
    return draft


def _seed_draft(resumes, filename, note="Drafted."):
    (resumes / filename).write_text("{}", encoding="utf-8")
    jobs_store.set_draft(JOB_TOP["id"], filename, "t1", note)


# --- the refactor: draft vs. draft-and-send --------------------------------

def _stub_turn(monkeypatch, reply="Emphasised your Python work."):
    async def fake_run_turn(prompt, session_id, model=None, **kw):
        (config.RESUMES_DIR / "made.json").write_text("{}", encoding="utf-8")
        return reply + "\n\n[[RESUME:made.json]]", "ses-new"

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(bot, "run_turn", fake_run_turn)
    monkeypatch.setattr(bot, "_keep_typing", noop)
    monkeypatch.setattr(bot, "_send_chat", noop)
    monkeypatch.setattr(bot, "_deliver_changed_resumes", noop)
    monkeypatch.setattr(config, "model_for", lambda task: None)


def test_a_quiet_draft_announces_nothing(store, resumes, monkeypatch):
    """announce=False is what makes the background draft invisible."""
    _stub_turn(monkeypatch)
    ctx = _Ctx()
    draft = asyncio.run(bot._draft_resume(ctx, 1, JOB_TOP, JOB_TOP["id"],
                                          announce=False))
    assert ctx.bot.sent == []
    assert draft["file"] == "made.json"
    assert "Emphasised your Python work" in draft["note"]


def test_the_apply_path_still_announces_and_decides(store, resumes, monkeypatch):
    """Regression on the split: the button's behaviour must be unchanged."""
    _stub_turn(monkeypatch)
    ctx = _Ctx()
    asyncio.run(bot._generate_resume_for(ctx, 1, JOB_TOP, JOB_TOP["id"]))
    assert any("Tailoring your resume" in t for t in ctx.bot.sent)
    assert jobs_store.get(JOB_TOP["id"])["state"] == "applied"


def test_a_failed_apply_still_tells_the_user(store, resumes, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("opencode fell over")

    monkeypatch.setattr(bot, "_draft_resume", boom)
    ctx = _Ctx()
    asyncio.run(bot._generate_resume_for(ctx, 1, JOB_TOP, JOB_TOP["id"]))
    assert any("Couldn't build the resume" in t for t in ctx.bot.sent)
    assert jobs_store.get(JOB_TOP["id"])["state"] == "offered"
