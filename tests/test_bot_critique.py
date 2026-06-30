import bot


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
