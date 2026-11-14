from app.service import load, remember


def test_load():
    assert load("a") == "a"


def test_remember():
    assert remember("k", 1) == 1
