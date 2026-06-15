import bot


def test_skip_reason_keyboard_uses_job_reasons():
    job = {"skip_reasons": ["Frontend role", "Needs 8y", "Onsite Penang"]}
    kb = bot._skip_reason_keyboard("abc123", job)
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "job:sk:abc123:0" in datas
    assert "job:sk:abc123:1" in datas
    assert "job:sk:abc123:2" in datas
    assert "job:sk:abc123:other" in datas
    assert "job:sk:abc123:none" in datas


def test_skip_reason_keyboard_falls_back_when_no_reasons():
    kb = bot._skip_reason_keyboard("abc123", {"skip_reasons": []})
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    # at least one fallback reason plus the two specials
    assert "✏️ Other…" in labels
    assert any(lbl in labels for lbl in ("Too senior", "Wrong tech", "Location"))


def test_skip_reason_keyboard_fallback_indices_present():
    # With no job-specific reasons, fallback buttons must still carry resolvable
    # numeric indices (0..n) so the handler can map them back to a reason label.
    kb = bot._skip_reason_keyboard("abc123", {"skip_reasons": []})
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "job:sk:abc123:0" in datas
