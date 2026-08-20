import config
import threads


def _isolate(tmp_path, monkeypatch):
    """Point the thread store at a temp dir (paths are read at call time)."""
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    return threads


CHAT = 4242


def test_missing_store_reads_as_empty(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    assert t.listing(CHAT) == []
    assert t.thread_for_message(CHAT, 1) is None


def test_corrupt_store_does_not_raise(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    p = t._path(CHAT)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert t.listing(CHAT) == []


def test_new_thread_is_retrievable_and_unique(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    a = t.new_thread(CHAT, "acme.com")
    b = t.new_thread(CHAT, "other.com")
    assert a != b
    assert t.get(CHAT, a)["label"] == "acme.com"
    assert t.exists(CHAT, a) and t.exists(CHAT, b)


def test_main_thread_always_exists_without_being_created(tmp_path, monkeypatch):
    """MAIN is the pre-thread conversation; it is never registered explicitly."""
    t = _isolate(tmp_path, monkeypatch)
    assert t.exists(CHAT, t.MAIN)
    assert t.get(CHAT, t.MAIN) is None


def test_unknown_thread_does_not_exist(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    assert not t.exists(CHAT, "tNOPE")


# --- reply routing ---------------------------------------------------------
def test_bound_message_routes_back_to_its_thread(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    key = t.new_thread(CHAT, "acme.com")
    t.bind_message(CHAT, 1001, key)
    assert t.thread_for_message(CHAT, 1001) == key


def test_binding_is_per_chat(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    key = t.new_thread(CHAT, "acme.com")
    t.bind_message(CHAT, 1001, key)
    assert t.thread_for_message(9999, 1001) is None


def test_bind_ignores_a_missing_message_id(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    t.bind_message(CHAT, None, "tX")
    assert t.thread_for_message(CHAT, None) is None


def test_message_bindings_are_capped_keeping_the_newest(tmp_path, monkeypatch):
    """The map is written on every bot message, so it must not grow forever."""
    t = _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(t, "_MAX_MESSAGE_BINDINGS", 5)
    key = t.new_thread(CHAT, "acme.com")
    for mid in range(1, 21):
        t.bind_message(CHAT, mid, key)
    assert t.thread_for_message(CHAT, 20) == key   # newest kept
    assert t.thread_for_message(CHAT, 1) is None   # oldest evicted
    assert len(t._load(CHAT)["messages"]) == 5


# --- labels + resume ownership --------------------------------------------
def test_url_label_strips_scheme_and_www():
    assert threads.label_for_url("https://www.jobstreet.com/job/1") == "jobstreet.com"
    assert threads.label_for_url("http://boards.acme.io/x") == "boards.acme.io"


def test_find_url_picks_the_first_link():
    assert threads.find_url("look at https://a.io/1 and https://b.io/2") == "https://a.io/1"
    assert threads.find_url("no links here") is None
    assert threads.find_url("") is None
    assert threads.find_url(None) is None


def test_format_label_puts_the_position_first():
    assert threads.format_label("Backend Developer", "Avanade") == \
        "Backend Developer — Avanade"


def test_format_label_drops_a_missing_side():
    assert threads.format_label("Backend Developer", "") == "Backend Developer"
    assert threads.format_label("", "Avanade") == "Avanade"
    assert threads.format_label("", "") == ""


def test_format_label_is_capped():
    label = threads.format_label("Senior " * 20, "Some Very Long Company Sdn Bhd")
    assert len(label) <= threads._MAX_LABEL


def test_set_label_names_a_provisional_thread(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    key = t.new_thread(CHAT, "jobstreet.com")
    assert t.get(CHAT, key)["named"] is False
    t.set_label(CHAT, key, "Backend Developer — Avanade")
    assert t.get(CHAT, key)["label"] == "Backend Developer — Avanade"
    assert t.get(CHAT, key)["named"] is True


def test_set_label_ignores_an_empty_name(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    key = t.new_thread(CHAT, "jobstreet.com")
    t.set_label(CHAT, key, "   ")
    assert t.get(CHAT, key)["label"] == "jobstreet.com"


def test_set_resume_records_ownership_and_renames_a_provisional_thread(tmp_path, monkeypatch):
    """A resume slug beats a bare hostname as a name."""
    t = _isolate(tmp_path, monkeypatch)
    key = t.new_thread(CHAT, "jobstreet.com")
    t.set_resume(CHAT, key, "avanade-backend.json")
    assert t.get(CHAT, key)["resume"] == "avanade-backend.json"
    assert t.get(CHAT, key)["label"] == "avanade-backend"   # host label upgraded


def test_set_resume_never_overwrites_a_real_name(tmp_path, monkeypatch):
    """'Backend Developer — Avanade' must not degrade to a filename slug."""
    t = _isolate(tmp_path, monkeypatch)
    key = t.new_thread(CHAT, "Backend Developer — Avanade", named=True)
    t.set_resume(CHAT, key, "avanade-backend.json")
    assert t.get(CHAT, key)["label"] == "Backend Developer — Avanade"
    assert t.get(CHAT, key)["resume"] == "avanade-backend.json"


def test_resume_owner_identifies_the_thread_that_owns_a_file(tmp_path, monkeypatch):
    """Delivery relies on this to keep one job's PDF out of another job's chat."""
    t = _isolate(tmp_path, monkeypatch)
    a = t.new_thread(CHAT, "a.com")
    b = t.new_thread(CHAT, "b.com")
    t.set_resume(CHAT, a, "acme.json")
    t.set_resume(CHAT, b, "globex.json")
    assert t.resume_owner(CHAT, "acme.json") == a
    assert t.resume_owner(CHAT, "globex.json") == b
    assert t.resume_owner(CHAT, "unclaimed.json") is None


def test_set_resume_on_an_unknown_thread_is_a_noop(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    t.set_resume(CHAT, "tGONE", "x.json")
    assert t.resume_owner(CHAT, "x.json") is None


# --- listing + forgetting --------------------------------------------------
def test_listing_is_most_recently_used_first(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    a = t.new_thread(CHAT, "a.com")
    b = t.new_thread(CHAT, "b.com")
    data = t._load(CHAT)
    data["threads"][a]["last_at"] = "2026-08-20T10:00:00+00:00"
    data["threads"][b]["last_at"] = "2026-08-19T10:00:00+00:00"
    t._save(CHAT, data)
    assert [k for k, _ in t.listing(CHAT)] == [a, b]


def test_touch_moves_a_thread_to_the_front(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    a = t.new_thread(CHAT, "a.com")
    b = t.new_thread(CHAT, "b.com")
    data = t._load(CHAT)
    data["threads"][a]["last_at"] = "2020-01-01T00:00:00+00:00"
    t._save(CHAT, data)
    assert [k for k, _ in t.listing(CHAT)][0] == b
    t.touch(CHAT, a)
    assert [k for k, _ in t.listing(CHAT)][0] == a


def test_forget_drops_the_thread_and_its_message_bindings(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    a = t.new_thread(CHAT, "a.com")
    b = t.new_thread(CHAT, "b.com")
    t.bind_message(CHAT, 11, a)
    t.bind_message(CHAT, 22, b)
    t.forget(CHAT, a)
    assert not t.exists(CHAT, a)
    assert t.thread_for_message(CHAT, 11) is None
    assert t.thread_for_message(CHAT, 22) == b   # sibling untouched


def test_forget_all_clears_everything(tmp_path, monkeypatch):
    t = _isolate(tmp_path, monkeypatch)
    key = t.new_thread(CHAT, "a.com")
    t.bind_message(CHAT, 11, key)
    t.forget_all(CHAT)
    assert t.listing(CHAT) == []
    assert t.thread_for_message(CHAT, 11) is None
