import { useState } from "react";
import { api, q } from "../api";
import { Badge, Empty, Err, RepoSelect, useRepos } from "../components";
import { nav } from "../route";

const EFFORT: Record<string, string> = { low: "good", medium: "warn", high: "bad" };

function copy(text: string, done: () => void) {
  navigator.clipboard?.writeText(text).then(done).catch(() => {});
}

function Hit({ h, target }: { h: any; target: string }) {
  const e = h.entry, f = h.fit;
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState("");
  const flash = (k: string) => { setCopied(k); setTimeout(() => setCopied(""), 1400); };
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div className="grow">
          <div style={{ fontWeight: 650, fontSize: 15 }}>{e.name} <span className="muted" style={{ fontWeight: 400 }}>from</span> <a onClick={() => nav("explorer", { repo: e.repo, symbol: e.id })}>{e.repo}</a></div>
          <div className="mono muted" style={{ overflowWrap: "anywhere" }}>{e.file}:{e.line}  {e.sig}</div>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <Badge>{Math.round(h.score * 100)}% match</Badge>
          <Badge kind={EFFORT[f.effort]}>{f.effort} effort to reuse</Badge>
        </div>
      </div>
      {e.doc && <p style={{ margin: "10px 0 0" }}>{e.doc}</p>}
      {f.notes.length > 0 && (
        <ul style={{ margin: "12px 0 0", paddingLeft: 18, color: "var(--text-2)" }}>
          {f.notes.map((n: string, i: number) => <li key={i} style={{ marginBottom: 3 }}>{n}</li>)}
        </ul>
      )}
      {f.notes.length === 0 && <p className="muted" style={{ margin: "10px 0 0" }}>Self-contained: copy it, keep an origin comment, done.</p>}
      <div className="chips" style={{ marginTop: 12 }}>
        {e.ext_imports.map((m: string) => <Badge key={m}>needs {m}</Badge>)}
        {e.tables.map((t: string) => <Badge key={t} kind="warn">table {t}</Badge>)}
        {e.tests.map((t: any) => <Badge key={t.name} kind="good">test {t.name}</Badge>)}
      </div>
      <div className="row" style={{ marginTop: 14 }}>
        <button className="primary" onClick={() => copy(h.prompt, () => flash("p"))}>{copied === "p" ? "Copied" : "Copy porting prompt for agent"}</button>
        <button onClick={() => copy(e.code, () => flash("c"))}>{copied === "c" ? "Copied" : "Copy source"}</button>
        <button onClick={() => setOpen(!open)}>{open ? "Hide source" : `Show source (${e.code.split("\n").length} lines${e.truncated ? ", truncated" : ""})`}</button>
        <button onClick={() => nav("impact", { repo: e.repo, target: e.id })}>Who uses it there</button>
      </div>
      {open && <pre className="code" style={{ margin: "12px 0 0", padding: 12, whiteSpace: "pre", overflow: "auto" }}>{e.code}</pre>}
    </div>
  );
}

export default function Reuse({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [into, setInto] = useState(params.into || "");
  const [need, setNeed] = useState(params.q || "");
  const [res, setRes] = useState<any[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const peers = (repos.data || []).filter((r) => r.indexed && r.name !== into).length;

  async function go() {
    setBusy(true); setErr("");
    try { setRes(await api.get(`/api/reuse?${q({ q: need, into })}`)); } catch (e: any) { setErr(e.message); setRes(null); }
    setBusy(false);
  }

  return (
    <>
      <h2>Reuse</h2>
      <p className="sub">Describe what you need to build. devmem searches every <i>other</i> onboarded repo for code that already does it, and tells you how much work it is to bring it into the repo you pick: missing packages, name clashes, tables it touches, helpers to copy along.</p>
      <div className="card">
        <div className="row">
          <span className="muted">Building in</span>
          {repos.data && <RepoSelect value={into} onChange={setInto} repos={repos.data} />}
          <input className="grow" placeholder="e.g. retry a failing HTTP call with backoff, parse SQL for table lineage…" value={need}
            onChange={(e) => setNeed(e.target.value)} onKeyDown={(e) => e.key === "Enter" && need.trim() && into && go()} aria-label="What do you need" />
          <button className="primary" disabled={!need.trim() || !into || busy} onClick={go}>{busy ? "Searching…" : "Find reusable code"}</button>
        </div>
        {repos.data && peers === 0 && <p className="muted" style={{ margin: "10px 0 0" }}>Only one repo is indexed. Onboard the repos you want to borrow from on the Repos page.</p>}
      </div>
      <Err msg={err} />
      {res && (res.length === 0
        ? <Empty>Nothing in your other repos matches. Write it fresh, or try different words.</Empty>
        : res.map((h) => <Hit key={h.entry.id + h.entry.repo} h={h} target={into} />))}
    </>
  );
}
