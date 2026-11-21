"""The model layer under real-world replies: retries, replies that wrap or omit the JSON, and retrieval before descriptions."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from devmem_app import chat


def test_json_list_is_found_in_fences_prose_and_wrapped_objects():
    assert chat.json_list('Here you go:\n```json\n[{"i": 0, "text": "a"}]\n```') == [{"i": 0, "text": "a"}]
    assert chat.json_list('See [1] and [2]. {"items": [{"i": 1}]}') == [{"i": 1}]
    assert chat.json_list("[]") == []
    assert chat.json_list("I could not describe these.") is None


def test_a_reply_without_the_list_is_asked_again_once():
    class Model:
        prompts: list = []

        def generate(self, prompt):
            self.prompts.append(prompt)
            return "Sure! Here are the descriptions." if len(self.prompts) == 1 else '[{"i": 0, "text": "ok"}]'

    m = Model()
    assert chat.ask_json_list(m, "describe") == [{"i": 0, "text": "ok"}]
    assert len(m.prompts) == 2 and "was not the JSON list" in m.prompts[1]
    m.prompts[:] = []
    m.generate = lambda p: (m.prompts.append(p), "no")[1]
    with pytest.raises(ValueError):
        chat.ask_json_list(m, "describe")
    assert len(m.prompts) == 2


def test_overloaded_and_rate_limited_answers_are_retried_but_bad_requests_are_not(monkeypatch):
    codes, hits = [529, 429, 200, 400], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            code = codes.pop(0)
            hits.append(code)
            ok = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
            data = json.dumps(ok if code == 200 else {"error": {"message": f"status {code}"}}).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        p = chat.HttpProvider("anthropic", "k", "claude-haiku-5-5", f"http://127.0.0.1:{server.server_port}")
        assert p.generate("hi") == "ok" and hits == [529, 429, 200]
        with pytest.raises(RuntimeError, match="HTTP 400: status 400"):
            p.generate("hi")
        assert hits == [529, 429, 200, 400]
    finally:
        server.shutdown()


def test_a_function_is_found_by_the_library_calls_in_its_body(tmp_path, monkeypatch):
    from devmem_app.indexer import index_repo

    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    (tmp_path / "api.py").write_text(
        "from sqlalchemy import create_engine\n\n\ndef get_rate(currency):\n"
        "    engine = create_engine('sqlite://')\n    return engine\n\n\ndef other():\n    return 1\n"
    )
    index_repo(tmp_path, "r")
    hits = chat.retrieve("where is the database engine created", {"r": Path(tmp_path)})
    assert hits and hits[0]["title"].startswith("def get_rate")


def test_definitions_the_model_skipped_are_asked_for_once_more(tmp_path, monkeypatch):
    from devmem.index import Index
    from devmem_app import definitions
    from devmem_app.indexer import index_repo

    monkeypatch.setenv("DEVMEM_HOME", str(tmp_path / "home"))
    (tmp_path / "m.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n\n\ndef c():\n    return 3\n")
    index_repo(tmp_path, "r")
    ix = Index.load(tmp_path)
    ids = [s["id"] for s in ix.data["symbols"] if s["kind"] == "function"]

    class Model:
        prompts: list = []

        def generate(self, prompt):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:   # skips one item and sends one malformed item
                return json.dumps([{"i": 0, "text": "Returns one."}, {"i": "x"}, {"i": 2, "text": "Returns three."}])
            return json.dumps([{"i": 0, "text": "Returns two."}])

    out = definitions.describe_with_ai(ix, ids, Model())
    assert len(out["written"]) == 3 and out["failed"] == [] and len(Model.prompts) == 2
