"""Classes that share method names (get, save), so a call resolves correctly only when the receiver's class is known."""
from __future__ import annotations
from typing import Optional


class Store:
    def get(self, key):
        return key

    def save(self, key, value):
        return value

    @classmethod
    def default(cls) -> "Store":
        return cls()

    def clone(self) -> "Store":
        return Store()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def backend(self) -> Backend:
        return Backend()


class Backend:
    def flush(self):
        pass

    def get(self, key):
        return None


class Cache:
    def get(self, key):
        return 1

    def save(self, key, value):
        return 2


class Base:
    def name(self):
        return "base"


class Child(Base):
    def special(self):
        return 1
