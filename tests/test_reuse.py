"""Cross-repo reuse: catalog, search, fit and the CLI/API surfaces."""
import json
import shutil
import subprocess
from pathlib import Path

from devmem import reuse as core
from devmem.index import Index
from devmem_app import reuse as app_reuse
from devmem_app.indexer import index_repo

FIX = Path(__file__).parent.parent / "fixtures"


def _repos(tmp_path):
    out = {}
    for fx in ("conv_app", "py_app"):
        d = tmp_path / fx
        shutil.copytree(FIX / fx, d)
        index_repo(d, fx)
        out[fx] = d
    return out


def test_live_reuse_finds_retry_in_other_repo(tmp_path):
    repos = _repos(tmp_path)
    hits = app_reuse.live("retry with backoff", "py_app", {k: str(v) for k, v in repos.items()} | {"py_app": str(repos["py_app"])})
    assert hits, "expected a hit from conv_app"
    e = hits[0]["entry"]
    assert e["repo"] == "conv_app" and "retry" in e["name"].lower() and e["code"]
    assert hits[0]["fit"]["effort"] in ("low", "medium", "high") and hits[0]["prompt"].startswith("Reuse `")


def test_fit_flags_clash_and_never_suggests_own_repo(tmp_path):
    repos = _repos(tmp_path)
    ix = Index.load(repos["py_app"])
    entries = app_reuse.build_entries(Index.load(repos["conv_app"]))
    assert all(e["repo"] == "conv_app" for e in entries)
    # py_app already defines a retry helper: same-name copy from conv_app must warn
    hits = core.reuse(ix, "retry", entries)
    assert hits and any("already exists" in n or "similar" in n for n in hits[0]["fit"]["notes"])
    assert core.reuse(Index.load(repos["conv_app"]), "retry", entries) == []   # own repo's entries are excluded


def test_publish_writes_catalog_and_agent_cli_uses_it(tmp_path, capsys):
    from devmem_app.publisher import publish
    from devmem.cli import main
    repos = _repos(tmp_path)
    peers = {k: str(v) for k, v in repos.items()}
    publish(repos["py_app"], index_repo(repos["py_app"], "py_app"), peers)
    cat = json.loads((repos["py_app"] / core.CATALOG).read_text())
    assert cat and all(e["repo"] == "conv_app" for e in cat)
    import os
    cwd = os.getcwd(); os.chdir(repos["py_app"])
    try:
        assert main(["reuse", "retry with backoff"]) == 0
        out = capsys.readouterr().out
        assert "conv_app:" in out and "effort to reuse" in out
        assert main(["recall", "retry with backoff"]) == 0
        assert "ALSO SOLVED IN OTHER REPOS" in capsys.readouterr().out
    finally:
        os.chdir(cwd)


def test_search_ranks_name_over_noise():
    es = [{"repo": "a", "name": "send_email", "sig": "", "doc": "", "file": "m.py", "line": 1},
          {"repo": "a", "name": "retry_with_backoff", "sig": "", "doc": "call again on failure", "file": "n.py", "line": 1}]
    assert core.search(es, "retry on failure")[0][1]["name"] == "retry_with_backoff"
    assert core.search(es, "zzzz") == []


# ---------- embeddings, summaries, central hub ----------
class FakeProvider:
    """Embeds by concept: 'reliable'/'flaky'/'retry' land on the same axis, so meaning (not shared words) decides."""
    name = "fake"
    def embed(self, texts):
        def v(t):
            t = t.lower()
            return [1.0 if any(w in t for w in ("retry", "reliable", "flaky", "backoff")) else 0.0,
                    1.0 if any(w in t for w in ("sql", "table")) else 0.0, 0.1]
        return [v(t) for t in texts]
    def generate(self, prompt):
        n = prompt.count("### ")
        return json.dumps([{"i": i, "summary": "makes unreliable calls dependable by trying again; retry, flaky, resilient"} for i in range(n)])


def test_embeddings_find_what_keywords_miss(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    repos = {k: str(v) for k, v in _repos(tmp_path).items()}
    need = "make unreliable network calls dependable"           # shares no word with `retry`
    assert app_reuse.live(need, "py_app", repos) == []
    hits = app_reuse.live(need, "py_app", repos, provider=FakeProvider())
    assert hits and "retry" in hits[0]["entry"]["name"].lower()


def test_summaries_make_offline_keyword_search_semantic(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    entries = app_reuse.build_entries(Index.load(_repos(tmp_path)["conv_app"]))
    need = "make unreliable calls dependable"
    assert core.search(entries, need) == []
    assert app_reuse.summarize(entries, FakeProvider()) == len(entries)
    assert core.search(entries, need)                           # pure BM25, no provider, no network
    assert app_reuse.summarize(entries, None) == len(entries)    # second run is served from the cache


def test_hub_roundtrip_and_agent_reads_it_offline(tmp_path, monkeypatch, capsys):
    from devmem import hub as agent_hub
    from devmem.cli import main
    from devmem_app import hub
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    repos = {k: str(v) for k, v in _repos(tmp_path).items()}
    hubdir = tmp_path / "central"
    hub.configure(str(hubdir))
    res = hub.push(repos)
    assert set(res["repos"]) == {"conv_app", "py_app"} and res["committed"]
    assert (hubdir / "catalog/conv_app.json").exists() and (hubdir / "repos/py_app/meta.json").exists() and (hubdir / "INDEX.md").exists()
    assert hub.push(repos).get("note") == "no changes since the last sync"
    assert not hub.configured_url()                              # a local path is never published to agents
    # an agent in a repo that has NO committed catalog, pointed at the hub by env
    work = tmp_path / "work"
    shutil.copytree(FIX / "py_app", work)
    index_repo(work, "work")
    monkeypatch.setenv("DEVMEM_HUB", str(hubdir))
    import os
    cwd = os.getcwd(); os.chdir(work)
    try:
        assert main(["reuse", "retry with backoff"]) == 0
        assert "conv_app:" in capsys.readouterr().out
        assert main(["hub", "repos"]) == 0
        out = capsys.readouterr().out
        assert "conv_app" in out and "reusable symbols" in out
    finally:
        os.chdir(cwd)
    assert agent_hub.spec(work) == str(hubdir)


def test_hub_url_is_validated_and_credentials_stripped(tmp_path, monkeypatch):
    from devmem_app import hub
    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    repo = tmp_path / "r"; repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://user:tok3n@github.com/o/r.git"], check=True)
    assert hub._origin(repo) == "https://github.com/o/r.git"
    assert hub.URL_OK.match("git@github.com:o/hub.git") and not hub.URL_OK.match("ext::sh -c id") and not hub.URL_OK.match("-oFoo=bar")
