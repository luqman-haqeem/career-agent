"""The opencode concurrency gate.

Measured on this box 2026-08-21: one run 6s; two concurrent 7-10s, clean over
six trials; THREE concurrent drove MemAvailable to 13 MB and all three were
reaped with zero bytes of output — including the run that started first. So an
extra run does not queue behind the others, it destroys them. These tests pin
the gate that makes that impossible.
"""
import asyncio

import pytest

import agent
import bot
import config


def test_the_cap_is_conservative_enough_for_this_box():
    assert 1 <= config.MAX_CONCURRENT_RUNS <= 2


def test_updates_may_outpace_runs_so_button_taps_stay_responsive():
    assert config.MAX_CONCURRENT_UPDATES >= config.MAX_CONCURRENT_RUNS


def test_the_gate_caps_simultaneous_runs(monkeypatch):
    """The whole point: never more than MAX_CONCURRENT_RUNS spawns at once."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 2)
    live = 0
    peak = 0

    async def fake_spawn(*a, **k):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return 0, "out", ""

    monkeypatch.setattr(agent, "_opencode_spawn", fake_spawn)

    async def main():
        await asyncio.gather(*(
            agent._opencode_invoke("hi", None, None) for _ in range(8)
        ))

    asyncio.run(main())
    assert peak == 2, f"gate let {peak} runs through at once"


def test_all_eight_runs_still_complete(monkeypatch):
    """Queued, not dropped — the gate delays work, it does not lose it."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 2)
    done = []

    async def fake_spawn(user_message, *a, **k):
        await asyncio.sleep(0.01)
        done.append(user_message)
        return 0, "out", ""

    monkeypatch.setattr(agent, "_opencode_spawn", fake_spawn)

    async def main():
        await asyncio.gather(*(
            agent._opencode_invoke(f"m{i}", None, None) for i in range(8)
        ))

    asyncio.run(main())
    assert sorted(done) == sorted(f"m{i}" for i in range(8))


def test_a_failing_run_releases_its_slot(monkeypatch):
    """A raised error must not leak a permit, or the bot deadlocks after N
    failures — exactly the silent wedge this work exists to prevent."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 1)

    async def boom(*a, **k):
        raise RuntimeError("opencode exceeded 1800s and was killed")

    monkeypatch.setattr(agent, "_opencode_spawn", boom)

    async def main():
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await agent._opencode_invoke("hi", None, None)
        # The gate must still be open.
        assert not agent._run_slots().locked()

    asyncio.run(main())


def test_a_cancelled_run_releases_its_slot(monkeypatch):
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 1)

    async def slow(*a, **k):
        await asyncio.sleep(10)

    monkeypatch.setattr(agent, "_opencode_spawn", slow)

    async def main():
        task = asyncio.create_task(agent._opencode_invoke("hi", None, None))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not agent._run_slots().locked()

    asyncio.run(main())


def test_the_gate_is_per_event_loop(monkeypatch):
    """Cached per loop, not at import — a stale loop's semaphore would wedge
    every later run."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 2)

    async def grab():
        return agent._run_slots()

    first = asyncio.run(grab())
    second = asyncio.run(grab())
    assert first is not second


def test_every_opencode_path_goes_through_the_gate():
    """A new caller must not be able to spawn opencode around the semaphore."""
    import inspect
    src = inspect.getsource(agent)
    callers = [ln.strip() for ln in src.splitlines()
               if "_opencode_spawn(" in ln and "async def" not in ln]
    assert callers == ["return await _opencode_spawn(user_message, session_id, files, model)"], \
        f"_opencode_spawn called outside the gate: {callers}"


def test_the_bot_raises_its_update_concurrency():
    """PTB defaults to 1, which serialised even trivial updates."""
    import inspect
    src = inspect.getsource(bot.main)
    assert "concurrent_updates(config.MAX_CONCURRENT_UPDATES)" in src
