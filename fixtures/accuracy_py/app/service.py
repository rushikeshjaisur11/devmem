import json

from app import http as h
from app.store import Cache as LRU
from app.store import make_store


def load(key):
    s = make_store()
    return s.get(key)


def fetch(url):
    return h.Session().get(url)


def config(d):
    out = {}
    for k, v in d.items():
        out[k] = v
    return json.dumps(out)


def remember(key, value):
    c = LRU()
    return c.put(key, value)


def run_all(keys):
    return list(map(load, keys))
