import { useState } from "react";
import { api } from "../api";
import { Badge, Bars, Empty, Err, LineChart, RepoSelect, useAsync, useRepos } from "../components";
import { nav } from "../route";

const TOOLS = ["ruff", "vulture", "radon", "deptry", "bandit", "sqlfluff", "clones"];

export default function Hygiene({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const [tool, setTool] = useState("");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const h = useAsync(() => (repo ? api.get(`/api/repos/${repo}/hygiene`) : Promise.resolve(null)), [repo]);
  const d = h.data;
  const run = async () => { setBusy(true); setErr(""); try { await api.post(`/api/repos/${repo}/hygiene`); h.reload(); } catch (e: any) { setErr(e.message); } setBusy(false); };
  const issues = (d?.issues || []).filter((i: any) => (!tool || i.tool === tool) && (!filter || (i.path + i.msg + i.code).toLowerCase().includes(filter.toLowerCase())));
  const trend = (d?.trend || []).map((t: any) => ({ x: t.ts.slice(0, 10), y: Object.values(t.counts as Record<string, number>).reduce((a, b) => a + b, 0) }));
  return (
    <>
      <div className="row" style={{ marginBottom: 6 }}>
        <h2 style={{ margin: 0 }}>Hygiene</h2>
        {repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}
        <button className="primary" disabled={!repo || busy} onClick={run}>{busy ? "Running tools…" : "Run hygiene"}</button>
      </div>
      <p className="sub">Lint, dead code, complexity, dependencies, security, SQL style and duplicated code, with the trend over runs.</p>
      <Err msg={h.error || err} />
      {d && !d.available && <Empty>No hygiene run yet for this repo. Press “Run hygiene”.</Empty>}
      {d?.available && <>
        <div className="card">
          <h3>Findings by tool</h3>
          <Bars rows={TOOLS.map((t) => ({ label: t, value: d.counts[t] || 0, sub: d.prev_counts?.[t] !== undefined && d.prev_counts[t] !== (d.counts[t] || 0) ? `(${(d.counts[t] || 0) - d.prev_counts[t] > 0 ? "+" : ""}${(d.counts[t] || 0) - d.prev_counts[t]})` : "" }))} />
          <div className="row" style={{ marginTop: 6 }}>{Object.entries(d.tools).filter(([, v]) => v !== "ok").map(([k, v]) => <Badge key={k} kind="warn">{k}: {String(v)}</Badge>)}<span className="muted">last run {d.ts}</span></div>
        </div>
        <div className="card"><h3>Total findings over runs</h3><LineChart points={trend} label="Total findings" /></div>
        <div className="card">
          <h3>Issues</h3>
          <div className="row" style={{ marginBottom: 8 }}>
            <select value={tool} onChange={(e) => setTool(e.target.value)} aria-label="Tool"><option value="">all tools</option>{TOOLS.filter((t) => t !== "clones").map((t) => <option key={t}>{t}</option>)}</select>
            <input className="grow" placeholder="filter by path, rule or message" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          <table><thead><tr><th>Tool</th><th>Rule</th><th>Where</th><th>Message</th></tr></thead><tbody>
            {issues.slice(0, 200).map((i: any, k: number) => (
              <tr key={k} className="click" onClick={() => nav("explorer", { repo, file: i.path })}><td>{i.tool}</td><td className="mono">{i.code}</td><td className="mono">{i.path}:{i.line}</td><td>{i.msg}{i.corroborated ? <span className="muted"> · no callers in graph</span> : null}</td></tr>
            ))}
          </tbody></table>
          {!issues.length && <Empty>No issues match.</Empty>}
        </div>
        {d.clones.length > 0 && <div className="card"><h3>Duplicated code</h3><table><thead><tr><th>A</th><th>B</th><th className="num">Tokens</th></tr></thead><tbody>
          {d.clones.map((c: any, k: number) => <tr key={k}><td className="mono">{c.a}:{c.a_lines.join("-")}</td><td className="mono">{c.b}:{c.b_lines.join("-")}</td><td className="num">{c.tokens}</td></tr>)}
        </tbody></table></div>}
      </>}
    </>
  );
}
