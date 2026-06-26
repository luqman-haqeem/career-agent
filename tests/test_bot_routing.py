import asyncio

import bot
import classify
import config


def test_model_for_message_none_when_routing_inactive(monkeypatch):
    monkeypatch.setattr(config, "routing_active", lambda: False)
    called = {"n": 0}

    async def fake_classify(text):
        called["n"] += 1
        return "critique"

    monkeypatch.setattr(classify, "classify_task", fake_classify)
    assert asyncio.run(bot._model_for_message("score it")) is None
    assert called["n"] == 0  # classifier must NOT be called when inactive


def test_model_for_message_uses_classifier_when_active(monkeypatch):
    monkeypatch.setattr(config, "routing_active", lambda: True)
    monkeypatch.setattr(bot.onboarding, "status", lambda: "done")

    async def fake_classify(text):
        return "critique"

    monkeypatch.setattr(classify, "classify_task", fake_classify)
    monkeypatch.setattr(config, "model_for", lambda task: f"model-{task}")
    assert asyncio.run(bot._model_for_message("score it")) == "model-critique"


def test_model_for_message_skips_classifier_during_onboarding(monkeypatch):
    monkeypatch.setattr(config, "routing_active", lambda: True)
    monkeypatch.setattr(bot.onboarding, "status", lambda: "in_progress")
    called = {"n": 0}

    async def fake_classify(text):
        called["n"] += 1
        return "critique"

    monkeypatch.setattr(classify, "classify_task", fake_classify)
    assert asyncio.run(bot._model_for_message("score it")) is None
    assert called["n"] == 0  # onboarding turns must not invoke the classifier
