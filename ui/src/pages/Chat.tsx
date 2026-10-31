import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Badge, EditorLink, Err, editorHref, useAsync, useRepos } from "../components";
import { nav } from "../route";

interface Msg { q: string; res?: any; err?: string; busy?: boolean; target?: string; force?: "ask" | "implement" }

const HINT = "Ask about your repos, describe something new to build, or paste a plan. Chat works out which.";

export default function Chat() {
  const repos = useRepos();
  const status = useAsync(() => api.get("/api/chat/status"), []);
  const [text, setText] = useState("");
  const [llm, setLlm] = useState(false);
  const [code, setCode] = useState(false);
  const [scope, setScope] = useState<string[]>([]);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [copied, setCopied] = useState(-1);
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (status.data?.configured) setLlm(true); }, [status.data]);
  useEffect(() => { const el = box.current; if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 280) + "px"; } }, [text]);

  async function send(q: string, opts: { target?: string; force?: "ask" | "implement"; at?: number } = {}) {
    const question = q.trim();
    if (!question) return;
    const i = opts.at ?? msgs.length;
    if (opts.at === undefined) { setText(""); setMsgs((m) => [...m, { q: question, busy: true }]); }
    else setMsgs((m) => m.map((x, j) => (j === i ? { ...x, busy: true, err: undefined } : x)));
    try {
      const res = await api.post("/api/chat", { question, llm, detail: code ? "code" : "pointers", target: opts.target, force: opts.force, repos: scope.length ? scope : undefined });
      setMsgs((m) => m.map((x, j) => (j === i ? { q: question, res, target: opts.target, force: opts.force } : x)));
    } catch (e: any) {
      setMsgs((m) => m.map((x, j) => (j === i ? { q: question, err: e.message } : x)));
    }
  }
  const open = (h: any) => h.kind === "doc" ? window.open(h.path, "_blank", "noopener") : h.kind === "lesson" || h.kind === "rule" ? nav("memory", { repo: h.repo })
    : nav("impact", { repo: h.repo, target: h.ref });
  const openable = (h: any) => ["doc", "lesson", "rule", "symbol", "test"].includes(h.kind);

  return (
    <>
      <h2>Chat</h2>
      <p className="sub">Talk to your repos. Questions get answers that point to code and current library docs. Something new to build, or a plan you wrote, gets a compact prompt for the agent that will do the work: what your org already built, what the libraries now provide built in, and what not to touch.</p>
      <div className="card">
        <textarea ref={box} value={text} rows={2} placeholder={HINT} aria-label="Message" style={{ minHeight: 56, resize: "none" }}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
        <div className="row" style={{ marginTop: 10, justifyContent: "space-between" }}>
          <span className="muted" style={{ fontSize: 12.5 }}>Enter to send · Shift+Enter for a new line</span>
          <button className="primary" disabled={!text.trim()} onClick={() => send(text)}>Send</button>
        </div>
        <details style={{ marginTop: 10 }}>
          <summary className="muted" style={{ cursor: "pointer", fontSize: 12.5 }}>Options</summary>
          <div className="row" style={{ marginTop: 8, gap: 18 }}>
            <label className="row"><input type="checkbox" checked={llm} disabled={!status.data?.configured} onChange={(e) => setLlm(e.target.checked)} />Write answers with {status.data?.model || "an LLM"}{!status.data?.configured && <span className="muted"> (set DEVMEM_VERTEX_PROJECT)</span>}</label>
            <label className="row"><input type="checkbox" checked={code} onChange={(e) => setCode(e.target.checked)} />Include source of existing code in agent prompts</label>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <span className="muted">Limit to repos:</span>
            {(repos.data || []).filter((r) => r.indexed).map((r) => (
              <label key={r.name} className="row"><input type="checkbox" checked={scope.includes(r.name)}
                onChange={(e) => setScope((s) => (e.target.checked ? [...s, r.name] : s.filter((x) => x !== r.name)))} />{r.name}</label>
            ))}
            <span className="muted">{scope.length ? "" : "(all)"}</span>
          </div>
        </details>
      </div>
      {msgs.length === 0 && <p className="muted">Try “who reads mart.sales_report”, “add retry with backoff to the export job”, or paste a step-by-step plan.</p>}
      {[...msgs].reverse().map((m, k) => {
        const idx = msgs.length - 1 - k, r = m.res;
        const impl = r?.intent === "implement";
        return (
          <div className="card" key={idx}>
            <div style={{ whiteSpace: "pre-wrap", fontWeight: 600, maxHeight: 120, overflow: "auto" }}>{m.q}</div>
            {m.busy && <p className="muted">Working…</p>}
            <Err msg={m.err || ""} />
            {r && (
              <div className="row" style={{ margin: "8px 0", gap: 8 }}>
                <Badge kind={impl ? "warn" : ""}>{impl ? "New requirement → agent prompt" : "Question"}</Badge>
                <span className="muted" style={{ fontSize: 12.5 }}>{r.reason}</span>
                <a style={{ fontSize: 12.5, marginLeft: "auto" }} onClick={() => send(m.q, { at: idx, target: m.target, force: impl ? "ask" : "implement" })}>{impl ? "Treat as a question instead" : "Make an agent prompt instead"}</a>
              </div>
            )}
            {r?.needs_repo && (
              <div>
                <p style={{ margin: "6px 0" }}>Which repo will the agent work in?</p>
                <div className="chips">{r.needs_repo.map((n: string) => <button key={n} className="chip" onClick={() => send(m.q, { at: idx, target: n, force: "implement" })}>{n}</button>)}</div>
              </div>
            )}
            {r && impl && !r.needs_repo && (
              <>
                <div className="row" style={{ justifyContent: "space-between", margin: "6px 0" }}>
                  <div className="chips">
                    <Badge>for {r.target}</Badge>
                    <Badge>~{r.tokens.toLocaleString()} tokens</Badge>
                    <Badge kind={r.counts.org_solutions ? "good" : ""}>{r.counts.org_solutions} existing solution(s)</Badge>
                    <Badge kind={r.counts.modernize ? "warn" : ""}>{r.counts.modernize} modernize</Badge>
                    <Badge kind={r.counts.doc_excerpts ? "good" : ""}>{r.counts.doc_excerpts} current-docs excerpt(s)</Badge>
                    {(r.docs_fetched || []).map((d: any) => <Badge key={d.source} kind={d.status === "fetched" ? "good" : d.status === "cached" ? "warn" : "bad"}>{d.name || d.source}: {d.status}</Badge>)}
                  </div>
                  <button className="primary" onClick={() => navigator.clipboard?.writeText(r.prompt).then(() => { setCopied(idx); setTimeout(() => setCopied(-1), 1400); })}>{copied === idx ? "Copied" : "Copy prompt"}</button>
                </div>
                {r.summary.some((s: any) => s.modernize.length) && r.summary.filter((s: any) => s.modernize.length).map((s: any) => s.modernize.map((x: string, i: number) =>
                  <p key={s.n + "-" + i} style={{ margin: "0 0 6px" }}><Badge kind="warn">modernize</Badge> {x}</p>))}
                {!r.counts.doc_excerpts && <p className="muted" style={{ fontSize: 12.5, margin: "4px 0" }}>No library docs matched. Add the libraries on the <a onClick={() => nav("docs")}>Library docs</a> page so the agent follows current APIs, not older org code.</p>}
                <pre className="code" style={{ padding: 12, whiteSpace: "pre-wrap", margin: "8px 0 0", maxHeight: 460 }}>{r.prompt}</pre>
                <p className="muted" style={{ marginBottom: 0, fontSize: 12.5 }}>Paste into Devin or any agent. Read-only: nothing was written to your repos.</p>
              </>
            )}
            {r && !impl && (
              <>
                <pre style={{ whiteSpace: "pre-wrap", font: "inherit", margin: "8px 0" }}>{r.answer}</pre>
                {r.llm_error && <p className="muted">LLM unavailable ({r.llm_error}); showing retrieval only.</p>}
                <div className="muted" style={{ marginBottom: 6 }}>{r.mode === "llm" ? `Answered by ${r.model}` : "Retrieval only"} · {r.hits.length} sources</div>
                {r.hits.map((h: any) => (
                  <details key={h.n} open={r.cited?.includes(h.n)} style={{ marginBottom: 6 }}>
                    <summary><span className="mono">[{h.n}]</span> <Badge>{h.kind}</Badge> {h.title} {openable(h) ? <a className="mono" href="#" onClick={(e) => { e.preventDefault(); open(h); }}>{h.pointer}</a> : <span className="mono">{h.pointer}</span>}{h.kind !== "doc" && <EditorLink href={editorHref(repos.data, h.repo, h.path, h.line)} />}</summary>
                    {h.snippet && <pre>{h.snippet}</pre>}
                  </details>
                ))}
              </>
            )}
          </div>
        );
      })}
    </>
  );
}
