import asyncio

import httpx

import classify


class _FakeResp:
    def __init__(self, status_code=200, content="default"):
        self.status_code = status_code
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        if self._exc:
            raise self._exc
        return self._resp


def _patch(monkeypatch, *, resp=None, exc=None, key="sk-test"):
    if key is None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENROUTER_API_KEY", key)
    monkeypatch.setattr(classify.httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(resp=resp, exc=exc))


def test_classify_returns_critique(monkeypatch):
    _patch(monkeypatch, resp=_FakeResp(content="critique"))
    assert asyncio.run(classify.classify_task("score my resume")) == "critique"


def test_classify_returns_resume(monkeypatch):
    _patch(monkeypatch, resp=_FakeResp(content="resume"))
    assert asyncio.run(classify.classify_task("write me a resume")) == "resume"


def test_classify_default_passes_through(monkeypatch):
    _patch(monkeypatch, resp=_FakeResp(content="default"))
    assert asyncio.run(classify.classify_task("how's the weather")) == "default"


def test_classify_unknown_output_falls_back(monkeypatch):
    _patch(monkeypatch, resp=_FakeResp(content="banana"))
    assert asyncio.run(classify.classify_task("hi")) == "default"


def test_classify_non_200_falls_back(monkeypatch):
    _patch(monkeypatch, resp=_FakeResp(status_code=500, content="critique"))
    assert asyncio.run(classify.classify_task("hi")) == "default"


def test_classify_exception_falls_back(monkeypatch):
    _patch(monkeypatch, exc=httpx.ConnectError("boom"))
    assert asyncio.run(classify.classify_task("hi")) == "default"


def test_classify_no_key_falls_back(monkeypatch):
    _patch(monkeypatch, resp=_FakeResp(content="critique"), key=None)
    assert asyncio.run(classify.classify_task("hi")) == "default"


def test_classify_empty_text_falls_back(monkeypatch):
    _patch(monkeypatch, resp=_FakeResp(content="critique"))
    assert asyncio.run(classify.classify_task("   ")) == "default"


def test_api_model_strips_openrouter_prefix(monkeypatch):
    monkeypatch.setattr(classify.config, "model_for",
                        lambda task: "openrouter/google/gemini-2.5-flash-lite")
    assert classify._api_model() == "google/gemini-2.5-flash-lite"


def test_api_model_passthrough_without_prefix(monkeypatch):
    monkeypatch.setattr(classify.config, "model_for",
                        lambda task: "google/gemini-2.5-flash-lite")
    assert classify._api_model() == "google/gemini-2.5-flash-lite"
