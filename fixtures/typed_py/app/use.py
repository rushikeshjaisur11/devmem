"""How real code tells you a receiver's class without a constructor call next to the use. Each call names its target."""
from __future__ import annotations
from typing import Optional, List
from .models import Base, Store, Cache, Backend


class Service:
    def __init__(self, store: Store, cache: Optional[Cache] = None, backends: List[Backend] = ()):
        self.store = store
        self.cache = cache
        self.backends = backends
        self._b: Backend | None = None

    def read(self, key):
        v = self.cache.get(key)            # Cache.get
        return v or self.store.get(key)    # Store.get

    def write(self, key, value):
        self.store.save(key, value)        # Store.save
        for b in self.backends:
            b.flush()                      # Backend.flush

    def lazy(self):
        self._b.flush()                    # Backend.flush


def opt(store: Optional[Store], cache: "Cache", maybe: Store | None):
    store.save(1, 2)                       # Store.save
    cache.save(1, 2)                       # Cache.save
    maybe.get(1)                           # Store.get


def factory():
    s = Store.default()
    s.save(1, 2)                           # Store.save
    s.backend.flush()                      # Backend.flush
    with Store() as st:
        st.get(1)                          # Store.get


def loop(stores: list[Store], by_name: dict[str, Cache]):
    for s in stores:
        s.get(1)                           # Store.get
    for c in by_name.values():
        c.save(1, 2)                       # Cache.save
    first = stores[0]
    first.save(1, 2)                       # Store.save


def walrus():
    if (s := Store()) is not None:
        s.get(1)                           # Store.get


def nested(store: Store):
    def inner():
        store.get(2)                       # Store.get
    return inner


def chain(store: Store):
    s = store
    s = s.clone()
    s.save(1, 2)                           # Store.save: clone() is declared to return a Store


def render(self: Store, key):
    return self.get(key)                   # Store.get: a plain function whose first parameter is named self


def narrowed(items: list[Base]):
    for it in items:
        if hasattr(it, "special"):
            it.special()                   # Child.special: declared as the base class, defined by a subclass


def branchy(flag):
    x = Store()
    if flag:
        x = Cache()
    x.save(1, 2)                           # Store.save or Cache.save: must not be reported as one certain target
