import { useEffect, useState } from "react";
import { api, q } from "../api";
import { nav } from "../route";
import { Badge, Empty, EditorLink, editorHref, Err, Icon, RepoSelect, useAsync, useRepos } from "../components";

const LEVEL: Record<string, [string, string]> = { low: ["bad", "low"], medium: ["warn", "medium"], high: ["good", "good"] };

function Row({ repo, it, repos, checked, onCheck, onSaved }: { repo: string; it: any; repos: any[] | null; checked: boolean; onCheck: (v: boolean) => void; onSaved: () => void }) {
  const [text, setText] = useState(it.description || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => setText(it.description || ""), [it.description]);
  const dirty = text.trim() !== (it.description || "");
  const save = async (value: string) => {
    setBusy(true); setErr("");
    try { await api.put(`/api/repos/${repo}/definitions`, { id: it.id, text: value }); onSaved(); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <div className="def">
      <input type="checkbox" checked={checked} onChange={(e) => onCheck(e.target.checked)} aria-label={`Select ${it.name}`} />
      <div className="def-main">
        <div className="row" style={{ gap: 8 }}>
          <span className="mono def-name">{it.name}</span>
          <Badge kind={LEVEL[it.level][0]}>{LEVEL[it.level][1]} confidence</Badge>
          {it.by && <Badge>{it.by === "ai" ? "written by AI" : "written by a person"}</Badge>}
          {it.callers > 0 && <span className="muted">used by {it.callers}</span>}
        </div>
        <div className="muted mono def-loc">{it.file}:{it.line} <EditorLink href={editorHref(repos, repo, it.file, it.line)} /></div>
        {it.sig && <div className="mono def-sig">{it.sig}</div>}
        {it.doc && <div className="def-doc">Docstring: {it.doc}</div>}
        {it.reasons.length > 0 && <div className="hint">Why: {it.reasons.join("; ")}.</div>}
        <div className="row def-edit">
          <input className="grow" value={text} maxLength={300} placeholder="One sentence: what it does and when to call it" aria-label={`Description of ${it.name}`} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && dirty && text.trim() && save(text)} />
          <button className={(dirty || it.by === "ai") && text.trim() ? "primary sm" : "sm"} disabled={busy || !text.trim() || (!dirty && it.by !== "ai")} title={it.by === "ai" && !dirty ? "Mark this draft as checked by a person" : ""} onClick={() => save(text)}><Icon name="check" />{it.by === "ai" && !dirty ? "Approve" : "Save"}</button>
          {it.description && <button className="ghost sm" disabled={busy} onClick={() => save("")}>Clear</button>}
        </div>
        {err && <div className="err" role="alert">{err}</div>}
      </div>
    </div>
  );
}

export default function Definitions({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const [level, setLevel] = useState("low");
  const [filter, setFilter] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const d = useAsync<any>(() => repo ? api.get(`/api/repos/${repo}/definitions?${q({ level, q: filter, limit: 100 })}`) : Promise.resolve(null), [repo, level, filter]);
  useEffect(() => { setSel(new Set()); setErr(""); }, [repo, level]);
  useEffect(() => setMsg(""), [repo]);
  const items: any[] = d.data?.items || [];
  const c = d.data?.counts;
  const llm = d.data?.llm;
  const todo = items.filter((i) => !i.description || i.stale);
  const pickTop = (n: number) => setSel(new Set(todo.slice(0, n).map((i) => i.id)));

  async function describe() {
    setBusy(true); setErr(""); setMsg("");
    try {
      const ids = [...sel].slice(0, 40);
      const r = await api.post(`/api/repos/${repo}/definitions/ai`, { ids });
      const n = Object.keys(r.written).length;
      setMsg(`${n} description${n === 1 ? "" : "s"} drafted${r.failed.length ? `, ${r.failed.length} could not be written${r.error ? ` (${r.error})` : ""}` : ""}.${n ? " They are listed below: correct any that are wrong, then approve them. Search uses them as written." : ""}`);
      setSel(new Set());
      if (n && level !== "described") setLevel("described"); else d.reload();
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  }

  return (
    <>
      <div className="row"><h1>Improve descriptions</h1>{repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}</div>
      <p className="sub">devmem builds its index without AI, so code with a vague name and no docstring can only be found by that name. This page lists the definitions it is least sure about, the most used first. Write one sentence for each, or let a model draft them and correct what is off. Descriptions are saved in <code>.devmem/descriptions.json</code> and searched like docstrings by agents. To draft them all in one go, with module summaries and an overview, use <a href={`#/analysis${repo ? `?repo=${repo}` : ""}`}>AI analysis</a>.</p>
      <Err msg={repos.error || d.error || err} />
      {msg && <div className="ok">{msg}</div>}
      {repos.data && !repos.data.some((r) => r.indexed) && <div className="card"><Empty>No indexed repo yet. <a href="#/repos">Add one</a> first.</Empty></div>}

      {c && (
        <div className="card">
          <div className="stats">
            <div className="stat"><b>{c.low}</b><span>low confidence: name says little, no docstring</span></div>
            <div className="stat"><b>{c.medium}</b><span>medium: only the name describes it</span></div>
            <div className="stat"><b>{c.high}</b><span>good: has a docstring or description</span></div>
            <div className="stat"><b>{d.data.described}</b><span>descriptions written here</span></div>
          </div>
        </div>
      )}

      {c && (
        <div className="card">
          <div className="toolbar">
            <div className="seg" role="tablist" aria-label="Which definitions to show">
              <button role="tab" aria-selected={level === "low"} className={level === "low" ? "on" : ""} onClick={() => setLevel("low")}>Low only</button>
              <button role="tab" aria-selected={level === "medium"} className={level === "medium" ? "on" : ""} onClick={() => setLevel("medium")}>Low and medium</button>
              <button role="tab" aria-selected={level === "described"} className={level === "described" ? "on" : ""} onClick={() => setLevel("described")}>Written ({d.data.described})</button>
            </div>
            <input className="grow" placeholder="Filter by file or name" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter" style={{ maxWidth: 280 }} />
            <span className="toolbar-end">
              <button className="ghost" disabled={!todo.length} onClick={() => pickTop(20)}>Select top 20</button>
              {sel.size > 0 && <button className="ghost" onClick={() => setSel(new Set())}>Clear selection</button>}
              {llm?.configured
                ? <button className="primary" disabled={busy || !sel.size} onClick={describe}><Icon name="spark" />{busy ? "Drafting…" : `Draft ${Math.min(sel.size, 40) || ""} with ${llm.model}`}</button>
                : <button onClick={() => nav("ai")} title="Optional: you can also type the descriptions yourself"><Icon name="spark" />Set up AI to draft them</button>}
            </span>
          </div>
          {sel.size > 40 && <p className="hint">The first 40 selected are drafted per run.</p>}
          {d.loading && !items.length ? <Empty>Loading…</Empty> : !items.length
            ? <Empty>{filter ? "Nothing matches that filter." : level === "described" ? "No descriptions written yet. Pick some definitions under “Low only” and describe them." : level === "low" ? "No low-confidence definitions here. Check “Low and medium” for the next tier." : "Every definition here is well described."}</Empty>
            : <div className="defs">{items.map((it) => <Row key={it.id} repo={repo} it={it} repos={repos.data} checked={sel.has(it.id)} onSaved={d.reload}
                onCheck={(v) => { const n = new Set(sel); v ? n.add(it.id) : n.delete(it.id); setSel(n); }} />)}</div>}
          {d.data.matching > items.length && <p className="hint" style={{ marginTop: 12 }}>Showing the {items.length} most used of {d.data.matching.toLocaleString()}. Describe these and the next ones appear.</p>}
        </div>
      )}
    </>
  );
}
