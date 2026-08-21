# Ensures the project root is on sys.path so tests can `import scan`, `import config`,
# etc. regardless of whether pytest is invoked as `pytest` or `python -m pytest`.
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    """No test may reach the internet.

    Wiring the JobStreet provider into run_scan quietly turned five scan tests
    into live API calls — the suite went from 1.9s to 16s and would have gone
    red the day JobStreet was down. Tests that mean to exercise the HTTP layer
    opt back in with @pytest.mark.network and stub the transport themselves.
    """
    if request.node.get_closest_marker("network"):
        return
    import httpx

    async def blocked(*a, **k):
        raise AssertionError(
            "a test tried to make a real HTTP request — stub it, or mark the "
            "test @pytest.mark.network")

    monkeypatch.setattr(httpx.AsyncClient, "get", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "post", blocked)
