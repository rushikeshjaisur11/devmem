import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Err, useAsync, useRepos } from "../components";
import { nav } from "../route";

interface Msg { q: string; res?: any; err?: string; busy?: boolean }

export default function Chat() {
  const repos = useRepos();
  const status = useAsync(() => api.get("/api/chat/status"), []);
  const [text, setText] = useState("");
  const [llm, setLlm] = useState(false);
  const [scope, setScope] = useState<string[]>([]);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  useEffect(() => { if (status.data?.configured) setLlm(true); }, [status.data]);

  async function ask() {
    const question = text.trim();
    if (!question) return;
    setText("");
    const i = msgs.length;
    setMsgs((m) => [...m, { q: question, busy: true }]);
    try {
      const res = await api.post("/api/chat", { question, llm, repos: scope.length ? scope : undefined });
      setMsgs((m) => m.map((x, j) => (j === i ? { q: question, res } : x)));
    } catch (e: any) {
      setMsgs((m) => m.map((x, j) => (j === i ? { q: question, err: e.message } : x)));
    }
  }
  const open = (h: any) => h.kind === "lesson" || h.kind === "rule" ? nav("memory", { repo: h.repo })
    : nav("explorer", { repo: h.repo, file: h.path, symbol: h.kind === "symbol" || h.kind === "test" ? h.ref : undefined });

  return (
    <>
      <h2>Chat</h2>
      <p className="sub">Ask about any onboarded repo: where something lives, who reads a table, what lesson already covers a problem. Answers point to code.</p>
      <div className="card">
        <div className="row">
          <input className="grow" value={text} placeholder="e.g. where do we retry failed requests?" aria-label="Question"
            onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} />
          <button className="primary" disabled={!text.trim()} onClick={ask}>Ask</button>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <label className="row"><input type="checkbox" checked={llm} disabled={!status.data?.configured} onChange={(e) => setLlm(e.target.checked)} />
            Write an answer with {status.data?.model || "an LLM"}</label>
          {!status.data?.configured && <span className="muted">Not configured: set DEVMEM_VERTEX_PROJECT to enable. Retrieval works without it.</span>}
          {llm && <span className="muted">Only the retrieved snippets are sent to the model.</span>}
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted">Repos:</span>
          {(repos.data || []).filter((r) => r.indexed).map((r) => (
            <label key={r.name} className="row"><input type="checkbox" checked={scope.includes(r.name)}
              onChange={(e) => setScope((s) => (e.target.checked ? [...s, r.name] : s.filter((x) => x !== r.name)))} />{r.name}</label>
          ))}
          <span className="muted">{scope.length ? "" : "(all)"}</span>
        </div>
      </div>
      {msgs.length === 0 && <p className="muted">Try “who reads mart.sales_report”, “slug helper”, or a problem a lesson might cover.</p>}
      {[...msgs].reverse().map((m, k) => (
        <div className="card" key={msgs.length - k}>
          <b>{m.q}</b>
          {m.busy && <p className="muted">Searching…</p>}
          <Err msg={m.err || ""} />
          {m.res && <>
            <pre style={{ whiteSpace: "pre-wrap", font: "inherit", margin: "8px 0" }}>{m.res.answer}</pre>
            {m.res.llm_error && <p className="muted">LLM unavailable ({m.res.llm_error}); showing retrieval only.</p>}
            <div className="muted" style={{ marginBottom: 6 }}>{m.res.mode === "llm" ? `Answered by ${m.res.model}` : "Retrieval only"} · {m.res.hits.length} sources</div>
            {m.res.hits.map((h: any) => (
              <details key={h.n} open={m.res.cited?.includes(h.n)} style={{ marginBottom: 6 }}>
                <summary><span className="mono">[{h.n}]</span> <Badge>{h.kind}</Badge> {h.title} <a className="mono" href="#" onClick={(e) => { e.preventDefault(); open(h); }}>{h.pointer}</a></summary>
                {h.snippet && <pre>{h.snippet}</pre>}
              </details>
            ))}
          </>}
        </div>
      ))}
    </>
  );
}
