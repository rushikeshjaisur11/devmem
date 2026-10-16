from app.retry import retry


class ApiClient:
    def __init__(self, base):
        self.base = base

    def get(self, path):
        """Fetch a path with retries."""
        return retry(lambda: self._raw(path))

    def _raw(self, path):
        return f"{self.base}/{path}"


def build_client(base):
    return ApiClient(base)
