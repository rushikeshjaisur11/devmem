import json

import pytest

from devmem.index import Index, _score, _tokens, write_db
from devmem.schema import DB_PATH, INDEX_PATH

NAMES = ["retryRequest", "load_user", "parse_config", "RetryPolicy.backoff", "send_mail", "Mailer.send", "t_retry"]


def make_idx():
    syms = [{"id": f"app/m{i}.py#{n}", "name": n.split(".")[-1], "qualname": n, "kind": "function",
             "file": f"app/m{i}.py", "line": i, "end_line": i + 1, "sig": "()", "doc": f"does {n} work with retry",
             "is_test": n.startswith("t_")} for i, n in enumerate(NAMES)]
    return {"version": 1, "repo": "r", "commit": None, "files": {}, "symbols": syms, "edges": []}


def brute(ix, query):
    q = _tokens(query)
    hits = [(sc, s) for s in ix.symbols.values() if s["kind"] != "module" and (sc := _score(q, query, s)) > 0]
    return sorted(hits, key=lambda x: (-x[0], x[1]["file"], x[1]["line"]))


@pytest.mark.parametrize("query", ["retry", "send mail", "ret", "Mailer.send", "backoff", "nothing here", "m3 py"])
def test_fts_find_matches_full_scan(tmp_path, query):
    (tmp_path / ".devmem").mkdir()
    (tmp_path / INDEX_PATH).write_text(json.dumps(make_idx()), encoding="utf-8")
    ix = Index.load(tmp_path)
    assert [(sc, s["id"]) for sc, s in ix.find(query, 100)] == [(sc, s["id"]) for sc, s in brute(ix, query)]


def test_readers_never_write_but_use_a_fresh_cached_db(tmp_path):
    (tmp_path / ".devmem").mkdir()
    p = tmp_path / INDEX_PATH
    p.write_text(json.dumps(make_idx()), encoding="utf-8")
    assert Index.load(tmp_path).find("parse config", 5)[0][1]["name"] == "parse_config"
    assert not (tmp_path / DB_PATH).exists()  # prompt building is read-only

    write_db(Index.load(tmp_path))
    assert "index.db" in (tmp_path / ".devmem/.gitignore").read_text()
    ix = Index.load(tmp_path)
    assert ix.find("parse config", 5)[0][1]["name"] == "parse_config"
    assert ix._fts.execute("PRAGMA database_list").fetchone()[2].endswith("index.db")  # file-backed, not in-memory


def test_stale_db_is_ignored_after_index_json_changes(tmp_path):
    (tmp_path / ".devmem").mkdir()
    p = tmp_path / INDEX_PATH
    p.write_text(json.dumps(make_idx()), encoding="utf-8")
    write_db(Index.load(tmp_path))
    data = make_idx()
    data["symbols"][2]["qualname"] = "read_settings"
    p.write_text(json.dumps(data), encoding="utf-8")
    ix = Index.load(tmp_path)
    assert ix.find("read settings", 5)[0][1]["qualname"] == "read_settings"
    assert all(s["qualname"] != "parse_config" for _, s in ix.find("parse config", 5))


def test_corrupt_db_falls_back_to_memory(tmp_path):
    (tmp_path / ".devmem").mkdir()
    (tmp_path / INDEX_PATH).write_text(json.dumps(make_idx()), encoding="utf-8")
    (tmp_path / DB_PATH).write_bytes(b"not a database" * 50)
    assert Index.load(tmp_path).find("send mail", 3)[0][1]["name"] == "send_mail"


def test_index_without_file_on_disk_uses_memory(tmp_path):
    assert Index(make_idx(), tmp_path).find("backoff", 3)[0][1]["name"] == "backoff"
    assert not (tmp_path / DB_PATH).exists()


def test_candidate_cap_keeps_the_best_match_for_common_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr("devmem.index.CANDIDATE_CAP", 5)
    data = make_idx()
    data["symbols"] += [{"id": f"x/h{i}.py#handler_{i}", "name": f"handler_{i}", "qualname": f"handler_{i}", "kind": "function",
                         "file": f"x/h{i}.py", "line": i, "end_line": i, "sig": "", "doc": "", "is_test": False} for i in range(60)]
    data["symbols"].append({"id": "x/r.py#retry_handler", "name": "retry_handler", "qualname": "retry_handler", "kind": "function",
                            "file": "x/r.py", "line": 1, "end_line": 1, "sig": "", "doc": "", "is_test": False})
    (tmp_path / ".devmem").mkdir()
    (tmp_path / INDEX_PATH).write_text(json.dumps(data), encoding="utf-8")
    ix = Index.load(tmp_path)
    assert len(ix._candidates(_tokens("retry handler"), "retry handler", 1)) <= 20  # 10 per source, not all 68
    assert ix.find("retry handler", 1)[0][1]["id"] == brute(ix, "retry handler")[0][1]["id"] == "x/r.py#retry_handler"
