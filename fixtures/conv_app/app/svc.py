import logging

from app.errors import NotFound
from app.retry import retry

log = logging.getLogger(__name__)
DATA = {"a": 1}


def get(key):
    """Fetch a value or raise NotFound."""
    try:
        return retry(lambda: DATA[key])
    except KeyError:
        log.warning("missing %s", key)
        raise NotFound(key)
