import sqlite3

from app.retry import retry


def connect(path):
    """Open a connection."""
    return retry(lambda: sqlite3.connect(path))
