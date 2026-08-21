"""The daily scan must survive being a few seconds late.

On 2026-08-21 the scheduled scan was skipped entirely: APScheduler's default
misfire window is 1 second, the process was busy at the fire time, and the run
was dropped 8.6s late with only a WARNING to show for it.
"""
import inspect
import re

import bot
import config


def test_grace_window_is_generous_enough_to_absorb_a_busy_moment():
    assert config.SCAN_MISFIRE_GRACE >= 600


def test_grace_window_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("SCAN_MISFIRE_GRACE", "42")
    import importlib
    reloaded = importlib.reload(config)
    try:
        assert reloaded.SCAN_MISFIRE_GRACE == 42
    finally:
        monkeypatch.delenv("SCAN_MISFIRE_GRACE", raising=False)
        importlib.reload(config)


def test_run_daily_passes_the_grace_window_to_apscheduler():
    """Without job_kwargs the 1-second default silently returns."""
    src = inspect.getsource(bot.main)
    call = re.search(r"run_daily\((.*?)\n            \)", src, re.S)
    assert call, "run_daily call not found in main()"
    assert "misfire_grace_time" in call.group(1)
    assert "SCAN_MISFIRE_GRACE" in call.group(1)


def test_a_late_scan_still_lands_on_the_right_weekday():
    """The weekday gate reads the clock at run time, so an hour's delay at
    09:00 cannot push a Monday scan into Tuesday."""
    assert config.SCAN_HOUR + config.SCAN_MISFIRE_GRACE / 3600 < 24
