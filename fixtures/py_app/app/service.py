from app import retry as retry_mod
from app.client import build_client


def load_user(uid):
    """Load a user record from the API."""
    c = build_client("http://x")
    return retry_mod.retry(lambda: c.get(f"users/{uid}"))


def main():
    print(load_user(1))
