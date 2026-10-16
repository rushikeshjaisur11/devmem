import time


def retry(fn, attempts=3):
    """Retry fn with a fixed delay."""
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            time.sleep(0.1)
