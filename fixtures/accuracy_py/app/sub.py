from app.store import Store


class RemoteStore(Store):
    def _load(self, key):
        return "remote:" + key

    def warm(self, keys):
        for k in keys:
            self.get(k)
