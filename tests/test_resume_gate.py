"""The score-before-send gate: a first draft is checked, and a draft that
cannot honestly clear the bar says so instead of shipping quietly."""
import asyncio

import pytest

import bot
import config
import resume_gate


# --- parsing ---------------------------------------------------------------
def test_parse_score_reads_marker_and_reason():
    score, reason = resume_gate.parse_score(
        "[[SCORE:86|thin AWS keyword coverage]]\nLead with the Bedrock work.")
    assert score == 86
    assert reason == "thin AWS keyword coverage"


def test_parse_score_marker_without_reason():
    assert resume_gate.parse_score("[[SCORE:91]]") == (91, None)


def test_parse_score_falls_back_to_a_prose_scorecard():
    # A turn that ignored the format still produced a usable number.
    score, reason = resume_gate.parse_score("📄 Acme Backend — **88/100**")
    assert score == 88
    assert reason is None


def test_parse_score_rejects_nonsense_and_missing_numbers():
    # Out of range means the model misread the format; passing 950 through
    # would clear the bar on garbage.
    assert resume_gate.parse_score("[[SCORE:950]]") == (None, None)
    assert resume_gate.parse_score("looks good to me!") == (None, None)
    assert resume_gate.parse_score("") == (None, None)
    assert resume_gate.parse_score(None) == (None, None)


def test_strip_marker_leaves_the_prose():
    assert resume_gate.strip_marker("[[SCORE:80|x]]\nadd metrics") == "add metrics"


# --- the verdict line ------------------------------------------------------
def test_verdict_states_the_score_when_it_clears():
    out = resume_gate.verdict(92, 90)
    assert "92/100" in out and "90" in out and out.startswith("✅")


def test_verdict_marks_a_revision():
    assert "Revised" in resume_gate.verdict(90, 90, revised=True)


def test_verdict_under_the_bar_names_the_cause_and_refuses_to_pad():
    out = resume_gate.verdict(84, 90, reason="no production Kubernetes")
    assert out.startswith("⚠️")
    assert "84/100" in out
    assert "no production Kubernetes" in out
    assert "didn't pad" in out


def test_verdict_says_so_when_scoring_produced_no_number():
    out = resume_gate.verdict(None, 90)
    assert "couldn't confirm" in out


def test_revise_prompt_forbids_inventing_the_gap_away():
    p = resume_gate.revise_prompt("acme-backend.json", 90, 82, "no metrics")
    assert "resumes/acme-backend.json" in p
    assert "82/100" in p and "90/100" in p
    assert "no metrics" in p
    assert "never fabricate" in p
    assert "leave it missing" in p


def test_score_prompt_pushes_back_on_grading_to_the_bar():
    p = resume_gate.score_prompt("acme-backend.json", 90)
    assert "resumes/acme-backend.json" in p
    assert "corrections.md" in p
    assert "do not grade generously to clear 90" in p


# --- the gate in the bot ---------------------------------------------------
@pytest.fixture
def gate_env(monkeypatch, tmp_path):
    """A written resume, a stubbed session store, and no real turns."""
    (tmp_path / "acme-backend.json").write_text("{}")
    monkeypatch.setattr(config, "RESUMES_DIR", tmp_path)
    monkeypatch.setattr(config, "RESUME_GATE_ENABLED", True)
    monkeypatch.setattr(config, "RESUME_TARGET_SCORE", 90)
    monkeypatch.setattr(config, "RESUME_GATE_MAX_REVISIONS", 1)
    monkeypatch.setattr(config, "model_for", lambda task: f"model-{task}")
    monkeypatch.setattr(bot, "load_session_id", lambda c, t=None: "sess-0")
    monkeypatch.setattr(bot, "save_session_id", lambda c, s, t=None: None)
    monkeypatch.setattr(bot.threads, "touch", lambda c, t: None)
    return tmp_path


def _stub_turns(monkeypatch, replies):
    """Answer successive run_turn calls from `replies`; record the prompts."""
    calls = []

    async def fake_run_turn(prompt, session_id, model=None, **kw):
        calls.append({"prompt": prompt, "model": model})
        return replies[len(calls) - 1], "sess-1"

    monkeypatch.setattr(bot, "run_turn", fake_run_turn)
    return calls


def test_gate_passes_a_draft_that_clears_the_bar_without_revising(monkeypatch, gate_env):
    calls = _stub_turns(monkeypatch, ["[[SCORE:93|clears the bar]]\nnone"])

    note = asyncio.run(bot._score_gate(123, "t1", "acme-backend.json"))

    assert len(calls) == 1                    # scored once, no revision run
    assert calls[0]["model"] == "model-critique"
    assert "93/100" in note and note.startswith("✅")


def test_gate_revises_once_then_reports_the_new_score(monkeypatch, gate_env):
    calls = _stub_turns(monkeypatch, [
        "[[SCORE:82|bullets read like duties]]\nQuantify the Bedrock work.",
        "[[SCORE:91|clears the bar]]\nRewrote the top four bullets.",
    ])

    note = asyncio.run(bot._score_gate(123, "t1", "acme-backend.json"))

    assert len(calls) == 2
    assert "82/100" in calls[1]["prompt"]     # the revision knows what it scored
    assert "91/100" in note
    assert "Revised" in note


def test_gate_stops_after_the_revision_budget_and_tells_the_user(monkeypatch, gate_env):
    calls = _stub_turns(monkeypatch, [
        "[[SCORE:80|no production Kubernetes]]\nNothing honest raises this.",
        "[[SCORE:84|no production Kubernetes]]\nTightened wording only.",
        "[[SCORE:99]]\nshould never run",
    ])

    note = asyncio.run(bot._score_gate(123, "t1", "acme-backend.json"))

    assert len(calls) == 2                    # one revision, not a loop
    assert note.startswith("⚠️")
    assert "84/100" in note
    assert "no production Kubernetes" in note


def test_gate_failure_never_costs_the_user_the_resume(monkeypatch, gate_env):
    async def boom(*a, **k):
        raise RuntimeError("opencode died")

    monkeypatch.setattr(bot, "run_turn", boom)

    assert asyncio.run(bot._score_gate(123, "t1", "acme-backend.json")) == ""


def test_gate_is_skipped_when_disabled_or_there_is_no_resume(monkeypatch, gate_env):
    async def fail(*a, **k):
        raise AssertionError("must not run a turn")

    monkeypatch.setattr(bot, "run_turn", fail)

    assert asyncio.run(bot._score_gate(123, "t1", "")) == ""
    assert asyncio.run(bot._score_gate(123, "t1", "never-written.json")) == ""
    monkeypatch.setattr(config, "RESUME_GATE_ENABLED", False)
    assert asyncio.run(bot._score_gate(123, "t1", "acme-backend.json")) == ""


def test_gated_scores_a_resume_the_turn_forgot_to_mark(monkeypatch, gate_env):
    """No [[RESUME:...]] marker is exactly the silent-82 case the gate is for."""
    _stub_turns(monkeypatch, ["[[SCORE:88|light on metrics]]\nAdd numbers."])
    monkeypatch.setattr(config, "RESUME_GATE_MAX_REVISIONS", 0)
    monkeypatch.setattr(bot, "_resume_snapshot",
                        lambda: {"acme-backend.json": 2.0})

    text, name = asyncio.run(
        bot._gated(123, "t1", "Here's your resume.", before={}, claimed=None))

    assert name == "acme-backend.json"
    assert text.startswith("Here's your resume.")
    assert "88/100" in text


def test_the_apply_draft_path_folds_the_verdict_into_its_note(monkeypatch, tmp_path):
    """End of the wire: the note the user reads carries the score."""
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)   # threads + sessions in tmp
    (tmp_path / "resumes").mkdir()
    monkeypatch.setattr(config, "RESUMES_DIR", tmp_path / "resumes")
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    monkeypatch.setattr(config, "RESUME_GATE_ENABLED", True)
    monkeypatch.setattr(config, "RESUME_TARGET_SCORE", 90)
    monkeypatch.setattr(config, "model_for", lambda task: None)

    replies = ["Emphasised your AWS work.\n\n[[RESUME:made.json]]",
               "[[SCORE:93|clears the bar]]\nnone"]
    calls = []

    async def fake_run_turn(prompt, session_id, model=None, **kw):
        calls.append(prompt)
        if len(calls) == 1:
            (config.RESUMES_DIR / "made.json").write_text("{}", encoding="utf-8")
        return replies[len(calls) - 1], "ses-new"

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(bot, "run_turn", fake_run_turn)
    monkeypatch.setattr(bot, "_keep_typing", noop)

    class _Ctx:
        class bot:  # noqa: N801
            @staticmethod
            async def send_message(*a, **kw):
                return None

    job = {"id": "j1", "title": "Backend Engineer", "company": "Acme",
           "location": "KL", "url": "https://example.com/j1"}
    draft = asyncio.run(bot._draft_resume(_Ctx(), 1, job, "j1", announce=False))

    assert len(calls) == 2                      # the draft, then the score pass
    assert "resumes/made.json" in calls[1]
    assert draft["file"] == "made.json"
    assert "Emphasised your AWS work" in draft["note"]
    assert "93/100" in draft["note"]
