import { useEffect, useState } from "react";
import { api, q } from "../api";
import { Badge, Empty, Err, RepoSelect, useRepos } from "../components";
import { nav } from "../route";

const List = ({ title, items, repo, hint }: { title: string; items: any[]; repo: string; hint?: string }) => (
  <div className="card"><h3>{title} <span className="muted">({items.length})</span></h3>{hint && <p className="muted" style={{ marginTop: 0 }}>{hint}</p>}
    {items.length ? <div className="list">{items.map((i) => {
      const id = typeof i === "string" ? i : i.id || i.job;
      return <a key={id} className="mono" onClick={() => id.includes("#") && nav("impact", { repo, target: id })}>{id}{i.conf ? <span className="muted"> [{i.conf}]</span> : null}</a>;
    })}</div> : <span className="muted">none</span>}
  </div>
);

export default function Impact({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const [target, setTarget] = useState(params.target || "");
  const [cross, setCross] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(t = target) {
    if (!t) return;
    setBusy(true); setErr(""); setRes(null);
    try { setRes(await api.get(cross ? `/api/impact-column?${q({ column: t })}` : `/api/repos/${repo}/impact?${q({ target: t })}`)); }
    catch (e: any) { setErr(e.message); }
    setBusy(false);
  }
  useEffect(() => { if (params.target && repo) run(params.target); }, [repo]); // eslint-disable-line

  const jobs = (xs: any[]) => xs.map((x) => ({ id: x.job, conf: x.conf }));
  return (
    <>
      <h2>Impact</h2>
      <p className="sub">Blast radius of a symbol, table or column. Hop 1 must be checked; hop 2 is for contract checks.</p>
      <div className="row" style={{ marginBottom: 12 }}>
        {repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}
        <input className="grow" placeholder="symbol, db.table, or db.table.column" value={target} onChange={(e) => setTarget(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()} aria-label="Impact target" />
        <label className="row"><input type="checkbox" checked={cross} onChange={(e) => setCross(e.target.checked)} /> column, across all repos</label>
        <button className="primary" disabled={!target || busy} onClick={() => run()}>{busy ? "…" : "Analyse"}</button>
      </div>
      <Err msg={err} />
      {!res && !err && <Empty>Enter a target. From the Explorer, use the Impact button on any symbol.</Empty>}
      {res?.kind === "symbol" && <>
        <div className="card"><span className="stat"><b>{res.hop1.length}</b><span>hop 1 callers</span></span><span className="stat"><b>{res.hop2.length}</b><span>hop 2</span></span><span className="stat"><b>{res.tests.length}</b><span>tests</span></span><span className="stat"><b>{res.files.length}</b><span>files</span></span>
          {res.capped?.length ? <div>{res.capped.map((c: any) => <Badge key={c.symbol} kind="warn">{c.symbol} has {c.callers} callers: widely used</Badge>)}</div> : null}</div>
        <List title="Hop 1: must check" items={res.hop1} repo={repo} />
        <List title="Hop 2: contract check" items={res.hop2} repo={repo} />
        <List title="Tests that reach it" items={res.tests} repo={repo} />
        <List title="Files in scope" items={res.files} repo={repo} />
        {res.cochange?.length ? <List title="Usually changes together with" items={res.cochange} repo={repo} /> : null}
      </>}
      {res?.kind === "table" && <>
        <List title={`${res.table} writers`} items={res.writers} repo={repo} />
        <List title={`${res.table} readers`} items={res.readers} repo={repo} />
      </>}
      {res?.kind === "column" && <>
        <List title={`Reads ${res.column}`} items={jobs(res.readers)} repo={repo} />
        <List title="May be affected (reads the whole table)" items={jobs(res.may_be_affected)} repo={repo} />
        {res.derived.map((d: any) => <div className="card" key={d.column}><h3>→ {d.column}</h3><p className="muted" style={{ marginTop: 0 }}>written by {d.via}</p>
          {d.readers.length ? <div className="list">{d.readers.map((r: any) => <span key={r.job} className="mono" style={{ display: "block" }}>{r.job} <span className="muted">[{r.conf}]</span></span>)}</div> : <span className="muted">no readers</span>}</div>)}
      </>}
    </>
  );
}
