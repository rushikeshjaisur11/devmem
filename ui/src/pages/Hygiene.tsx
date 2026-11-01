import { useState } from "react";
import { api } from "../api";
import { Badge, Copy, EditorLink, Empty, Err, LineChart, RepoSelect, Term, editorHref, useAsync, useRepos } from "../components";

/** What each tool measures, in the words a reader uses. */
const KINDS: [string, string][] = [["ruff", "Lint"], ["vulture", "Dead code"], ["radon", "Complexity"], ["bandit", "Security"],
  ["deptry", "Dependencies"], ["sqlfluff", "SQL style"], ["clones", "Duplicated code"]];
const KIND = Object.fromEntries(KINDS);

const top = (xs: string[], n: number) => Object.entries(xs.reduce((m, x) => ({ ...m, [x]: (m[x] || 0) + 1 }), {} as Record<string, number>)).sort((a, b) => b[1] - a[1]).slice(0, n);

export default function Hygiene({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const [tool, setTool] = useState("");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState("");
  const h = useAsync(() => (repo ? api.get(`/api/repos/${repo}/hygiene`) : Promise.resolve(null)), [repo]);
  const d = h.data;
  const gaps = useAsync(() => (repo ? api.get(`/api/repos/${repo}/gaps?limit=8`) : Promise.resolve([])), [repo]);
  const run = async () => { setBusy(true); setErr(""); try { await api.post(`/api/repos/${repo}/hygiene`); h.reload(); } catch (e: any) { setErr(e.message); } setBusy(false); };
  const all: any[] = d?.issues || [];
  const scoped = all.filter((i) => !tool || i.tool === tool);
  const issues = scoped.filter((i) => !filter || (i.path + i.msg + i.code).toLowerCase().includes(filter.toLowerCase()));
  const trend = (d?.trend || []).map((t: any) => ({ x: t.ts.slice(0, 10), y: Object.values(t.counts as Record<string, number>).reduce((a, b) => a + b, 0) }));
  const total = d ? Object.values(d.counts as Record<string, number>).reduce((a, b) => a + b, 0) : 0;
  const prevTotal = d?.prev_counts && Object.keys(d.prev_counts).length ? Object.values(d.prev_counts as Record<string, number>).reduce((a, b) => a + b, 0) : null;
  const delta = (now: number, before?: number) => before === undefined || before === null || before === now ? null : now - before;
  const copyPrompt = async (file: string, n: number) => {
    const text = `Fix the ${n} hygiene finding${n === 1 ? "" : "s"} in ${file}. Run \`devmem hygiene ${file}\` for the list. Change only what each finding needs, then run \`devmem check\` and \`devmem diffcheck\`.`;
    try { await navigator.clipboard.writeText(text); setCopied(file); } catch { setErr(text); }
  };

  return (
    <>
      <div className="row">
        <h1>Code health</h1>
        {repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}
        <button className="primary" disabled={!repo || busy} onClick={run}>{busy ? "Running tools…" : "Run hygiene"}</button>
      </div>
      <p className="sub">Lint, dead code, complexity, security, dependency, SQL style and duplicate findings. Agents see the findings for a file with <code>devmem hygiene &lt;file&gt;</code> before they touch it.</p>
      <Err msg={h.error || err} />
      {d && !d.available && <div className="card"><Empty>No hygiene run for {repo} yet. Run hygiene to scan it; the tools only read the code.</Empty></div>}
      {d?.available && <>
        <p className="lede" style={{ fontSize: 20 }}>
          <b>{total.toLocaleString()} findings</b> in {repo}
          {prevTotal !== null && delta(total, prevTotal) !== null && <>, {Math.abs(delta(total, prevTotal)!)} {delta(total, prevTotal)! > 0 ? "more" : "fewer"} than the run before</>}.{" "}
          <span className="muted">Last run {d.ts.slice(0, 16).replace("T", " ")}.</span>
        </p>
        <div className="hy-tiles" role="group" aria-label="Filter by kind">
          {KINDS.filter(([t]) => t !== "clones").map(([t, label]) => {
            const n = d.counts[t] || 0, dl = delta(n, d.prev_counts?.[t]), state = d.tools[t];
            return (
              <button key={t} className={`hy-tile ${tool === t ? "on" : ""}`} aria-pressed={tool === t} disabled={state !== "ok" && !n} onClick={() => setTool(tool === t ? "" : t)}
                title={state !== "ok" ? `${t}: ${state}` : `${t}: show only these`}>
                <span className="k">{label}</span>
                <b>{state === "ok" || n ? n : "–"}</b>
                <span className="t">{t}{dl !== null && <em className={dl > 0 ? "up" : "down"}>{dl > 0 ? "+" : ""}{dl}</em>}{state !== "ok" && !n && <> · {String(state)}</>}</span>
              </button>
            );
          })}
        </div>
        <div className="hy-split">
          <div className="card">
            <h3>Start with these files</h3>
            {top(scoped.map((i) => i.path), 8).map(([f, n]) => (
              <div className="hy-file" key={f}>
                <a className="mono" onClick={() => setFilter(f)} title="Show only this file">{f}</a>
                <span className="muted">{n}</span>
                <EditorLink href={editorHref(repos.data, repo, f)} />
                <button className="quiet" onClick={() => copyPrompt(f, n)}>{copied === f ? "Copied" : "Copy fix prompt"}</button>
              </div>
            ))}
            {!scoped.length && <Empty>Nothing here.</Empty>}
          </div>
          <div className="card">
            <h3>Most frequent rules</h3>
            <div className="tags">{top(scoped.map((i) => `${i.tool} ${i.code}`), 14).map(([r, n]) => <button key={r} className="chip" onClick={() => setFilter(r.split(" ")[1])} title={scoped.find((i) => `${i.tool} ${i.code}` === r)?.msg}>{r.split(" ")[1]}<span className="n">{n}</span></button>)}</div>
            {trend.length > 1 && <div style={{ marginTop: 18 }}><LineChart points={trend} label="Total findings" height={150} /></div>}
          </div>
        </div>
        {(gaps.data || []).length > 0 && <div className="card">
          <h3><Term k="gap">Untested code</Term> that many places call</h3>
          <p className="hint" style={{ marginTop: -6 }}>A bug here spreads to every caller and no test would catch it. One test for each of the top few is the cheapest safety you can add. Agents get this list from <code>devmem gaps</code>.</p>
          {gaps.data.map((g: any) => (
            <div className="hy-file" key={g.id}>
              <span className="mono" title={g.doc || undefined}>{g.id.split("#")[1]} <span className="muted">{g.file}:{g.line}</span></span>
              <span className="muted">{g.callers} callers in {g.files} file{g.files === 1 ? "" : "s"}</span>
              <EditorLink href={editorHref(repos.data, repo, g.file, g.line)} />
              <Copy label="Copy test prompt" text={`Write focused tests for ${g.id} (${g.callers} callers, no test reaches it). Run \`devmem show ${g.id}\` to read it and \`devmem impact ${g.id}\` for how callers use it. Cover the behaviour callers rely on; change no production code.`} />
            </div>
          ))}
        </div>}
        <div className="card">
          <div className="row" style={{ marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>{tool ? KIND[tool] : "All findings"}</h3>
            <span className="muted">{issues.length === scoped.length ? issues.length : `${issues.length} of ${scoped.length}`}</span>
            <span className="grow" />
            <input style={{ width: 320 }} placeholder="Filter by path, rule or message" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter findings" />
            {(tool || filter) && <button className="quiet" onClick={() => { setTool(""); setFilter(""); }}>Clear</button>}
          </div>
          {issues.length ? <table><thead><tr><th>Kind</th><th>Rule</th><th>Where</th><th>Message</th></tr></thead><tbody>
            {issues.slice(0, 200).map((i: any, k: number) => (
              <tr key={k}><td><Badge kind={i.tool === "bandit" ? "bad" : ""}>{KIND[i.tool] || i.tool}</Badge></td><td className="mono">{i.code}</td><td className="mono">{i.path}:{i.line}<EditorLink href={editorHref(repos.data, repo, i.path, i.line)} /></td><td>{i.msg}{i.corroborated ? <span className="muted"> (no callers in the graph either)</span> : null}</td></tr>
            ))}
          </tbody></table> : <Empty>No findings match. <a onClick={() => { setTool(""); setFilter(""); }}>Clear the filters</a>.</Empty>}
          {issues.length > 200 && <p className="hint">Showing the first 200. Narrow with a kind or the filter.</p>}
        </div>
        {d.clones.length > 0 && <div className="card"><h3>Duplicated code <span className="muted" style={{ fontWeight: 400 }}>({d.clones.length} pairs)</span></h3><table><thead><tr><th>First copy</th><th>Second copy</th><th className="num">Size (tokens)</th></tr></thead><tbody>
          {d.clones.map((c: any, k: number) => <tr key={k}><td className="mono">{c.a}:{c.a_lines.join("-")}</td><td className="mono">{c.b}:{c.b_lines.join("-")}</td><td className="num">{c.tokens}</td></tr>)}
        </tbody></table></div>}
      </>}
    </>
  );
}
