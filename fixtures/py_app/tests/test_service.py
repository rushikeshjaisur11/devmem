from app.service import load_user


def test_load_user():
    assert "users/1" in load_user(1)
