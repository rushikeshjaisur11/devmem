import os


def env():
    """Current environment name."""
    return os.getenv("APP_ENV", "dev")
