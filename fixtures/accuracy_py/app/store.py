class Store:
    def get(self, key):
        return self._load(key)

    def _load(self, key):
        return key

    def items(self):
        return []


class Cache:
    def get(self, key):
        return None

    def put(self, key, value):
        return value


def make_store():
    return Store()
