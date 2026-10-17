import pytest


@pytest.fixture(autouse=True)
def _fast_indexing_by_default(monkeypatch):
    """Production indexing runs SCIP (Node + network) and Graphify and refuses to continue without them. Tests index hundreds of
    times offline, so they opt out explicitly; tests/test_accuracy.py exercises the strict path with stand-ins for the tools."""
    monkeypatch.setenv("DEVMEM_ACCURACY", "fast")
