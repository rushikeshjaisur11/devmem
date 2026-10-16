"""Retry helper."""
import time


def retry(fn, attempts=3, delay=0.1):
    """Call fn, retrying on exception."""
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(delay)
