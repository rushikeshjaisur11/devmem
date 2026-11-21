import pytest


@pytest.fixture(autouse=True)
def _fast_indexing_by_default(monkeypatch):
    """Production indexing runs SCIP (Node + network) and Graphify and refuses to continue without them. Tests index hundreds of
    times offline, so they opt out explicitly; tests/test_accuracy.py exercises the strict path with stand-ins for the tools."""
    monkeypatch.setenv("DEVMEM_ACCURACY", "fast")


@pytest.fixture(autouse=True)
def _no_model_keys_from_the_machine(monkeypatch):
    """A model key on the developer's machine would turn "no model is set up" tests into real model calls."""
    from devmem_app import settings
    names = {*settings.ENV.values(), *(v for spec in settings.PROVIDERS.values() for v in spec[4]),
             "GOOGLE_API_KEY", "DEVMEM_VERTEX_LOCATION", "DEVMEM_VERTEX_MODEL", "DEVMEM_VERTEX_EMBED_MODEL"}
    for name in names:
        monkeypatch.delenv(name, raising=False)
