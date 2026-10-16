import pytest

from app.errors import NotFound
from app.svc import get


def test_get(key):
    assert get(key) == 1


def test_missing():
    with pytest.raises(NotFound):
        get("zzz")
