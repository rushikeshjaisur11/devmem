import { useEffect, useState } from "react";
import { api, q } from "../api";
import { Badge, Copy, EditorLink, editorHref, Empty, Err, useRepo, useRepos } from "../components";
import { nav } from "../route";

const EFFORT: Record<string, [string, string]> = { low: ["good", "Little work to reuse"], medium: ["warn", "Some work to reuse"], high: ["bad", "A lot of work to reuse"] };
const EXAMPLES = ["retry with backoff", "parse a config file", "send an email", "upload a file to storage"];

function Hit({ h, into, repos }: { h: any; into: string; repos: any[] | null }) {
  const e = h.entry, f = h.fit;
  const [kind, label] = EFFORT[f.effort] || ["", f.effort];
  return (
    <section className="card reuse-hit">
      <div className="reuse-head">
        <div className="grow">
          <h3 className="mono">{e.name}</h3>
          <div className="muted reuse-where"><b>{e.repo}</b> · <span className="mono">{e.file}:{e.line}</span><EditorLink href={editorHref(repos, e.repo, e.file, e.line)} /></div>
        </div>
        <Badge kind={kind}>{label}</Badge>
      </div>
      {e.doc && <p className="reuse-doc">{e.doc}</p>}
      {e.sig && <pre className="out reuse-sig">{e.sig}</pre>}
      {(f.modernize.length > 0 || f.notes.length > 0) && <>
        <h4>To bring it into {into}</h4>
        <ul className="reuse-notes">
          {f.modernize.map((n: string, i: number) => <li key={"m" + i}>Prefer the library built-in: {n}</li>)}
          {f.notes.map((n: string, i: number) => <li key={i}>{n[0].toUpperCase() + n.slice(1)}</li>)}
        </ul>
      </>}
      {e.tests.length > 0 && <p className="muted reuse-tests">Shown working in {e.tests.map((t: any) => t.name).join(", ")}</p>}
      <div className="row reuse-actions">
        <Copy text={h.prompt} label="Copy agent prompt" />
        <button onClick={() => nav("impact", { repo: e.repo, target: e.id })}>What depends on it in {e.repo}</button>
        <span className="grow" />
        <span className="muted num">match {h.score.toFixed(2)}</span>
      </div>
      {e.code && <details className="reuse-code"><summary>Source ({e.code.split("\n").length} lines{e.truncated ? ", first part" : ""})</summary><pre className="out">{e.code}</pre></details>}
    </section>
  );
}

export default function Reuse({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const { repo: into } = useRepo();
  const [need, setNeed] = useState(params.need || "");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const indexed = (repos.data || []).filter((r) => r.indexed);

  async function run(n = need) {
    if (!n.trim() || !into) return;
    setBusy(true); setErr("");
    try { setRes(await api.get(`/api/reuse?${q({ need: n.trim(), into })}`)); history.replaceState(null, "", `#/reuse?${q({ repo: into, need: n.trim() })}`); }   // shareable URL without remounting the page
    catch (e: any) { setErr(e.message); setRes(null); }
    setBusy(false);
  }
  useEffect(() => { if (params.need && into) run(params.need); }, [into]); // eslint-disable-line

  return (
    <>
      <h1>Already built elsewhere</h1>
      <p className="sub">Before writing something new, see whether another of your repos already has it, and how much work it is to bring over.</p>
      <form className="gtools" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <div className="search grow">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input value={need} onChange={(e) => setNeed(e.target.value)} placeholder="What do you need? e.g. retry failed uploads with backoff" aria-label="What you need" />
        </div>
        <span className="muted">into <b style={{ color: "var(--text)" }}>{into || "…"}</b> (change the repo in the top bar)</span>
        <button className="primary" type="submit" disabled={busy || !need.trim() || !into}>{busy ? "Searching…" : "Search other repos"}</button>
      </form>
      <Err msg={err || repos.error} />
      {repos.data && indexed.length < 2 ? (
        <div className="card"><Empty>Reuse needs at least two indexed repos: this one and the one to borrow from. <a href="#/repos">Add another repo</a>.</Empty></div>
      ) : !res ? (
        <div className="card">
          <h3>Try a need</h3>
          <div className="examples">{EXAMPLES.map((x) => <button type="button" key={x} className="chip" onClick={() => { setNeed(x); run(x); }}>{x}</button>)}</div>
          <p className="hint" style={{ marginBottom: 0 }}>Describe what the code should do, not its name. Agents run the same search with <code>devmem reuse</code> or the <code>reuse</code> MCP tool.</p>
        </div>
      ) : res.hits.length === 0 ? (
        <div className="card"><Empty>Nothing in the other {res.peers} repo{res.peers === 1 ? "" : "s"} matches “{need}”. Write it fresh, or describe it differently.</Empty></div>
      ) : (
        <>
          <p className="muted">{res.hits.length} match{res.hits.length === 1 ? "" : "es"} in {res.peers} other repo{res.peers === 1 ? "" : "s"}, best first.</p>
          {res.hits.map((h: any) => <Hit key={h.entry.repo + h.entry.id} h={h} into={into} repos={repos.data} />)}
        </>
      )}
    </>
  );
}
