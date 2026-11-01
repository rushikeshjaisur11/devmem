import { useState } from "react";
import { api } from "../api";
import { Badge, Bars, EditorLink, Empty, Err, RepoSelect, editorHref, useAsync, useRepos } from "../components";

const fmt = (v: any) => (v === null || v === undefined ? "–" : v);

function PrMetrics({ d, onUploaded }: { d: any; onUploaded: () => void }) {
  const [msg, setMsg] = useState("");
  const rows: [string, (x: any) => any][] = [["Commits", (x) => x.n], ["Median lines changed", (x) => x.median_lines], ["90th percentile lines", (x) => x.p90_lines],
    ["Median files", (x) => x.median_files], ["Within diff budget", (x) => (x.within_budget === undefined ? undefined : `${x.within_budget}%`)], ["Reverts", (x) => x.reverts]];
  const cols: [string, any][] = d.split ? [["Agent before", d.agent.before], ["Agent after", d.agent.after], ["Human before", d.human.before], ["Human after", d.human.after]]
    : [["Agent", d.agent.all], ["Human", d.human.all]];
  const bars = d.recent.filter((r: any) => r.agent).slice(0, 12).map((r: any) => ({ label: r.sha, value: r.lines }));
  async function upload(f: File | undefined) {
    if (!f) return;
    try { const r = await api.post("/api/metrics/devin", JSON.parse(await f.text())); setMsg(`${r.new} new sessions (${r.total} total)`); onUploaded(); }
    catch (e: any) { setMsg(e.message); }
  }
  return (
    <>
      <div className="card"><h3>Agent changes {d.split ? `before vs after devmem (${d.split.slice(0, 10)})` : ""}</h3>
        {d.commits_scanned === 0 ? <Empty>No git history found for this repo.</Empty> : <>
          <p className="muted" style={{ marginTop: 0 }}>{d.agent_commits} of the last {d.commits_scanned} commits look agent-made (author or Co-authored-by matches Devin, Copilot, Claude or a bot). Diff budget: {d.budget} lines. Set <span className="mono">metrics.agent_pattern</span> or <span className="mono">metrics.since</span> in .devmem/config.yaml to override.</p>
          <table><thead><tr><th></th>{cols.map(([c]) => <th key={c} className="num">{c}</th>)}</tr></thead><tbody>
            {rows.map(([label, f]) => <tr key={label}><td>{label}</td>{cols.map(([c, x]) => <td key={c} className="num">{fmt(f(x))}</td>)}</tr>)}
          </tbody></table>
          {!d.split && <p className="muted">devmem was not found in the history, so there is no before/after split yet.</p>}
        </>}
      </div>
      {bars.length > 0 && <div className="card"><h3>Lines changed per recent agent commit</h3><Bars rows={bars} /></div>}
      <div className="card"><h3>PR reports (devmem report)</h3>
        {d.runs.n === 0 ? <Empty>No reports yet. Agents run <span className="mono">devmem report</span> before opening a PR and commit .devmem/metrics/.</Empty> : <>
          <p className="muted" style={{ marginTop: 0 }}>{d.runs.n} reports, {d.runs.clean} clean. Median reported tokens: {fmt(d.runs.median_tokens)}. Most blocked checks: {Object.entries(d.runs.blocked_checks).map(([k, v]) => `${k} ×${v}`).join(", ") || "none"}.</p>
          <table><thead><tr><th>When</th><th>Task</th><th className="num">Files</th><th className="num">+/-</th><th className="num">Tokens</th><th className="num" title="devmem answers / whole files they point to">devmem tokens</th><th>Blocked</th></tr></thead><tbody>
            {d.runs.recent.map((r: any) => <tr key={r.ts + r.head}><td>{r.ts.slice(0, 16)}</td><td>{r.task || "–"}</td><td className="num">{r.files}</td><td className="num">+{r.added}/-{r.removed}</td><td className="num">{fmt(r.tokens)}</td><td className="num">{r.devmem?.calls ? `${fmt(r.devmem.out_tokens)} / ~${fmt(r.devmem.file_tokens)}` : "–"}</td><td>{r.violations.blocked.join(", ") || "–"}</td></tr>)}
          </tbody></table></>}
      </div>
      <div className="card"><h3>Devin sessions</h3>
        {d.devin ? <table><thead><tr><th></th>{Object.keys(d.devin).map((k) => <th key={k} className="num">{k}</th>)}</tr></thead><tbody>
          {[["Sessions", "sessions"], ["With a PR", "with_pr"], ["Median ACUs", "acus_median"], ["Total ACUs", "acus_total"]].map(([l, k]) => <tr key={k}><td>{l}</td>{Object.values(d.devin).map((v: any, i) => <td key={i} className="num">{fmt(v[k])}</td>)}</tr>)}
        </tbody></table> : <Empty>No session data. If you use Devin, export its sessions as JSON and upload them here; nothing is fetched over the network.</Empty>}
        <div className="row" style={{ marginTop: 8 }}><label>Upload session export <input type="file" accept=".json" onChange={(e) => upload(e.target.files?.[0])} /></label><span className="muted">{msg}</span></div>
      </div>
    </>
  );
}

export default function Metrics({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const m = useAsync(() => (repo ? api.get(`/api/repos/${repo}/metrics`) : Promise.resolve(null)), [repo]);
  const d = m.data;
  const usage = (repos.data || []).find((r) => r.name === repo)?.usage;
  const pm = d?.pr_metrics;
  const agentNow = pm ? (pm.split ? pm.agent.after : pm.agent.all) : null;
  return (
    <>
      <div className="row" style={{ marginBottom: 6 }}><h1>Results</h1>{repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}</div>
      <p className="sub">Is devmem paying off? Smaller agent changes, fewer tokens, and lessons agents found useful.</p>
      <Err msg={m.error} />
      {d && <>
        <p className="lede" style={{ fontSize: 20 }}>
          {usage?.calls ? <>Agents asked devmem <b>{usage.calls.toLocaleString()} questions</b> here and got <mark>{usage.out_tokens.toLocaleString()} tokens</mark> of answers instead of ~{usage.file_tokens.toLocaleString()} tokens of files. </> : <>No agent queries logged on this machine yet. </>}
          {pm?.split && pm.agent.before.median_lines != null && pm.agent.after.median_lines != null
            ? <>Agent commits changed a median of <b>{pm.agent.after.median_lines} lines</b> since devmem, compared with {pm.agent.before.median_lines} before.</>
            : agentNow?.median_lines != null ? <>Agent commits change a median of <b>{agentNow.median_lines} lines</b>.</> : null}
        </p>
        <div className="card stats">
          {[["symbols indexed", d.symbols], ["tests", d.tests], ["files", d.files], ["lessons", d.lessons], ["rules", d.rules], ["lessons rated useful", d.feedback.useful], ["rated not useful", d.feedback.useless]].map(([k, v]) => <span className="stat" key={k as string}><b>{(v as number).toLocaleString()}</b><span>{k}</span></span>)}
        </div>
        <div className="card"><h3>Knowledge freshness</h3>
          {d.knowledge_problems.length === 0 ? <Badge kind="good">all knowledge files fresh and within budget</Badge> : <ul style={{ margin: 0, paddingLeft: 18 }}>{d.knowledge_problems.map((p: string) => <li key={p}>{p}</li>)}</ul>}
        </div>
        <div className="card"><h3>Files that change most (edit with care)</h3>
          {d.hotspots.length ? <table><thead><tr><th>File</th><th className="num">Commits</th><th className="num">Bug fixes</th><th className="num">Authors</th><th>Last change</th></tr></thead><tbody>
            {d.hotspots.map((h: any) => <tr key={h.file}><td className="mono">{h.file}<EditorLink href={editorHref(repos.data, repo, h.file)} /></td><td className="num">{h.commits}</td><td className="num">{h.bugfix}</td><td className="num">{h.authors}</td><td>{h.last}</td></tr>)}
          </tbody></table> : <Empty>No hotspots yet: publish a repo that has git history.</Empty>}
        </div>
        <PrMetrics d={d.pr_metrics} onUploaded={m.reload} />
      </>}
    </>
  );
}
