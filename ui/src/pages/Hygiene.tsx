import { useEffect, useMemo, useState } from "react";
import { api, upload } from "../api";
import { nav } from "../route";
import { Badge, CopyPrompt, EditorLink, Empty, Err, Icon, LineChart, useRepo, Term, editorHref, useAsync, useRepos } from "../components";

const SEV: Record<string, [string, string]> = { high: ["High", "bad"], medium: ["Medium", "warn"], low: ["Low", ""] };
const TOOL: Record<string, string> = { sonar: "SonarQube", ruff: "ruff", vulture: "vulture", radon: "radon", bandit: "bandit", deptry: "deptry", sqlfluff: "sqlfluff", eslint: "eslint", knip: "knip" };
const TOOL_JOB: [string, string][] = [["ruff", "Lint"], ["vulture", "Dead code"], ["radon", "Complexity"], ["bandit", "Security"], ["deptry", "Dependencies"], ["sqlfluff", "SQL style"], ["eslint", "JS/TS lint"], ["knip", "Unused JS/TS"]];
const RATING = ["", "A", "B", "C", "D", "E"];
const PAGE = 60;

/** Share of findings per severity: one bar, a 2px gap between segments, and a labelled legend that doubles as the filter. */
function SeverityBar({ s, on, pick }: { s: any; on: string; pick: (k: string) => void }) {
  const total = s.total || 1;
  return (
    <div className="sevbar-wrap">
      <div className="sevbar" role="img" aria-label={`${s.high} high, ${s.medium} medium, ${s.low} low severity findings`}>
        {(["high", "medium", "low"] as const).filter((k) => s[k] > 0).map((k) => <span key={k} className={`seg-${k}`} style={{ flexGrow: s[k] / total }} title={`${SEV[k][0]}: ${s[k].toLocaleString()}`} />)}
      </div>
      <div className="sevlegend">
        {(["high", "medium", "low"] as const).map((k) => (
          <button key={k} className={`chip ${on === k ? "on" : ""}`} aria-pressed={on === k} disabled={!s[k]} onClick={() => pick(on === k ? "" : k)}>
            <i className={`dot seg-${k}`} />{SEV[k][0]}<span className="n">{s[k].toLocaleString()}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

const Tile = ({ label, value, sub, kind = "" }: { label: string; value: any; sub?: any; kind?: string }) => (
  <div className="kpi"><span className="k">{label}</span><b className={kind}>{value}</b>{sub && <span className="t">{sub}</span>}</div>
);

export default function Hygiene({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const { repo } = useRepo();
  const [view, setView] = useState("first");
  const [sev, setSev] = useState("");
  const [cat, setCat] = useState("");
  const [src, setSrc] = useState("");
  const [text, setText] = useState("");
  const [shown, setShown] = useState(PAGE);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const h = useAsync<any>(() => (repo ? api.get(`/api/repos/${repo}/health`) : Promise.resolve(null)), [repo]);
  const cov = useAsync<any>(() => (repo ? api.get(`/api/repos/${repo}/coverage`) : Promise.resolve(null)), [repo]);
  const sendCoverage = async (f: File) => {
    setBusy("cov"); setErr(""); setMsg("");
    try {
      const r = await upload(`/api/repos/${repo}/coverage?filename=${encodeURIComponent(f.name)}`, f, () => {});
      setMsg(`${r.format} report imported: ${r.stats.covered} of ${r.stats.functions} functions ran in tests` + (r.per_test ? `; per-test data for ${r.stats.with_tests}, so test selection is now measured.` : ". It has no per-test data, so test selection still uses the call graph.")
        + (r.stats.newer_than_report ? ` ${r.stats.newer_than_report} file(s) changed after the run and were skipped.` : ""));
      cov.reload(); h.reload();
    } catch (e: any) { setErr(e.message); }
    setBusy("");
  };
  const d = h.data;
  useEffect(() => { setSev(""); setCat(""); setSrc(""); setText(""); setMsg(""); setErr(""); setView("first"); }, [repo]);
  useEffect(() => setShown(PAGE), [sev, cat, src, text, view, repo]);

  const run = async (what: "local" | "sonar") => {
    setBusy(what); setErr(""); setMsg("");
    try {
      if (what === "local") { await api.post(`/api/repos/${repo}/hygiene`); setMsg("Checks finished."); }
      else { const r = await api.post(`/api/repos/${repo}/sonar/sync`); setMsg(`Read ${r.issues.toLocaleString()} open issue${r.issues === 1 ? "" : "s"} and ${r.hotspots} hotspot${r.hotspots === 1 ? "" : "s"} from SonarQube.${r.notes.length ? " Note: " + r.notes.join("; ") : ""}`); }
      h.reload();
    } catch (e: any) { setErr(e.message); }
    setBusy("");
  };

  const all: any[] = d?.findings || [];
  const list = useMemo(() => {
    const q = text.trim().toLowerCase();
    return all.filter((f) => (!sev || f.severity === sev) && (!cat || f.category === cat) && (!src || (src === "local" ? f.source !== "sonar" : f.source === src))
      && (!q || `${f.path} ${f.code} ${f.msg} ${f.symbol || ""}`.toLowerCase().includes(q)));
  }, [all, sev, cat, src, text]);
  const filtered = !!(sev || cat || src || text);
  const clear = () => { setSev(""); setCat(""); setSrc(""); setText(""); };
  const sq = d?.sonar, setup = d?.sonar_setup, m = sq?.measures || {};
  const canSync = setup?.configured && (setup.project.key || setup.project.detected);
  const header = (
    <>
      <div className="row">
        <h1>Code health</h1>
        <span className="grow" />
        <button className={d?.available ? "" : "primary"} disabled={!repo || !!busy} onClick={() => run("local")} title="ruff, vulture, radon, bandit, deptry, sqlfluff, eslint, knip: they only read the code"><Icon name="shield" />{busy === "local" ? "Running checks…" : d?.local ? "Run checks again" : "Run checks"}</button>
        {canSync
          ? <button disabled={!repo || !!busy} onClick={() => run("sonar")}><Icon name="refresh" />{busy === "sonar" ? "Reading SonarQube…" : sq ? "Sync SonarQube" : "Read from SonarQube"}</button>
          : <button onClick={() => nav("integrations")}><Icon name="plug" />Connect SonarQube</button>}
      </div>
      <p className="sub">Every finding from devmem's checks and from SonarQube in one list, ordered by what to fix first: how severe it is, how many places call the code it is in, and whether a test covers that code. Agents see a file's findings with <code>devmem hygiene &lt;file&gt;</code> before they touch it.</p>
      <Err msg={repos.error || h.error || err} />
      {msg && <div className="ok">{msg}</div>}
    </>
  );
  if (!d) return header;
  if (!d.available) return <>{header}<div className="card"><Empty>Nothing has checked {repo} yet. Run the built-in checks, or connect SonarQube to bring in what it already found.</Empty></div></>;

  const s = d.summary;
  return (
    <>
      {header}
      <div className="card">
        <div className="kpis">
          <Tile label="Open findings" value={s.total.toLocaleString()} sub={`${d.sources.length} source${d.sources.length === 1 ? "" : "s"}`} />
          <Tile label="Fix first" value={s.risky.toLocaleString()} kind={s.risky ? "bad" : ""} sub="serious, widely used, no test" />
          {sq?.gate?.status && <Tile label="Quality gate" value={sq.gate.status === "OK" ? "Passed" : sq.gate.status === "ERROR" ? "Failed" : sq.gate.status} kind={sq.gate.status === "ERROR" ? "bad" : sq.gate.status === "OK" ? "good" : ""} sub={sq.gate.failed?.length ? `${sq.gate.failed.length} condition${sq.gate.failed.length === 1 ? "" : "s"} failed` : "SonarQube"} />}
          {m.coverage !== undefined && <Tile label="Test coverage" value={`${Number(m.coverage).toFixed(1)}%`} sub="SonarQube" />}
          {m.duplicated_lines_density !== undefined && <Tile label="Duplicated lines" value={`${Number(m.duplicated_lines_density).toFixed(1)}%`} sub="SonarQube" />}
          {!sq && <Tile label="Duplicated blocks" value={d.clones.length} sub="pairs found" />}
          <Tile label="Untested hot spots" value={d.gaps.length >= 10 ? "10+" : d.gaps.length} sub="called often, no test" />
        </div>
        <SeverityBar s={s} on={sev} pick={(k) => { setSev(k); setView("first"); }} />
        {sq?.gate?.failed?.length > 0 && <p className="hint" style={{ margin: "12px 0 0" }}>Quality gate failed on: {sq.gate.failed.map((c: any) => `${c.metric.replace(/_/g, " ")} (${c.actual}, limit ${c.threshold})`).join("; ")}.</p>}
      </div>

      <div className="seg big" role="tablist" style={{ marginBottom: 16 }}>
        {[["first", "Fix first", list.length], ["files", "By file", d.files.length], ["gaps", "Untested code", d.gaps.length], ["dupes", "Duplicates", d.clones.length], ["sources", "Sources", null]].map(([id, label, n]) => (
          <button key={id as string} role="tab" aria-selected={view === id} className={view === id ? "on" : ""} onClick={() => setView(id as string)}>{label}{n !== null && <span className="count">{(n as number).toLocaleString()}</span>}</button>
        ))}
      </div>

      {view === "first" && (
        <div className="card">
          <div className="toolbar">
            <select value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Kind of problem">
              <option value="">All kinds</option>
              {d.categories.map((c: any) => <option key={c.id} value={c.id}>{c.label} ({c.count.toLocaleString()})</option>)}
            </select>
            <select value={src} onChange={(e) => setSrc(e.target.value)} aria-label="Source">
              <option value="">All sources</option>
              {d.sources.some((x: any) => x.id === "sonar") && d.sources.length > 1 && <option value="local">devmem checks</option>}
              {d.sources.map((x: any) => <option key={x.id} value={x.id}>{TOOL[x.id] || x.id} ({x.count.toLocaleString()})</option>)}
            </select>
            <input className="grow" style={{ maxWidth: 340 }} placeholder="Filter by file, rule, function or message" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter findings" />
            {filtered && <button className="ghost" onClick={clear}>Clear filters</button>}
            <span className="toolbar-end muted">{filtered ? `${list.length.toLocaleString()} of ${all.length.toLocaleString()}` : `${all.length.toLocaleString()} findings`}{d.summary.total > d.shown ? `, the ${d.shown.toLocaleString()} highest priority loaded` : ""}</span>
          </div>
          {!list.length ? <Empty>{filtered ? <>No findings match. <a onClick={clear}>Clear the filters</a>.</> : "No findings. Nothing to fix here."}</Empty> : (
            <div className="finds">
              {list.slice(0, shown).map((f, k) => (
                <div className="find" key={k}>
                  <span className={`badge sev ${SEV[f.severity][1]}`}><i className={`dot seg-${f.severity}`} />{SEV[f.severity][0]}</span>
                  <div className="find-main">
                    <div className="find-msg">{f.msg}</div>
                    <div className="find-where">
                      {f.symbol && <span className="mono sym">{f.symbol}</span>}
                      <span className="mono muted">{f.path}:{f.line}</span>
                      <EditorLink href={editorHref(repos.data, repo, f.path, f.line)} />
                    </div>
                    <div className="tags">
                      <span className="tag">{TOOL[f.source] || f.source}</span>
                      <button className="tag link mono" title="Show every finding of this rule" onClick={() => setText(f.code)}>{f.code}</button>
                      <span className="tag">{d.categories.find((c: any) => c.id === f.category)?.label || f.category}</span>
                      {f.callers > 0 && <span className="tag" title="Places that call the function this finding is in">used by <b>{f.callers}</b></span>}
                      {f.untested && f.callers > 0 && <span className="tag warn" title="No test calls this function, directly or through one caller">no test</span>}
                      {f.effort && <span className="tag" title="SonarQube's estimate of the time to fix">{f.effort}</span>}
                    </div>
                  </div>
                  <div className="find-act">
                    <CopyPrompt repo={repo} kind="finding" data={f} />
                    {f.url && <a className="btn-link" href={f.url} target="_blank" rel="noreferrer">Open in SonarQube</a>}
                  </div>
                </div>
              ))}
            </div>
          )}
          {list.length > shown && <div className="row" style={{ marginTop: 14, justifyContent: "center" }}><button onClick={() => setShown(shown + PAGE * 2)}>Show more ({(list.length - shown).toLocaleString()} left)</button></div>}
        </div>
      )}

      {view === "files" && (
        <div className="card">
          <p className="hint" style={{ marginTop: 0 }}>Files ordered by the combined priority of their findings. One prompt fixes a whole file.</p>
          <table>
            <thead><tr><th>File</th><th>Findings</th><th className="num">High</th><th className="num">Medium</th><th className="num">Low</th><th className="num">Used by</th>{sq && <th className="num">Coverage</th>}<th></th></tr></thead>
            <tbody>
              {d.files.map((f: any) => (
                <tr key={f.path}>
                  <td className="mono"><a onClick={() => { clear(); setText(f.path); setView("first"); }} title="Show this file's findings">{f.path}</a> <EditorLink href={editorHref(repos.data, repo, f.path)} /></td>
                  <td style={{ width: 160 }}><div className="sevbar sm" role="img" aria-label={`${f.high} high, ${f.medium} medium, ${f.low} low`}>{(["high", "medium", "low"] as const).filter((k) => f[k] > 0).map((k) => <span key={k} className={`seg-${k}`} style={{ flexGrow: f[k] }} />)}</div></td>
                  <td className="num">{f.high || <span className="muted">0</span>}</td><td className="num">{f.medium || <span className="muted">0</span>}</td><td className="num">{f.low || <span className="muted">0</span>}</td>
                  <td className="num" title="Other files that call code in this file">{f.used_by ? `${f.used_by} file${f.used_by === 1 ? "" : "s"}` : <span className="muted">-</span>}</td>
                  {sq && <td className="num">{f.coverage === null || f.coverage === undefined ? <span className="muted">-</span> : `${f.coverage.toFixed(0)}%`}</td>}
                  <td><div className="row actions"><CopyPrompt repo={repo} kind="file" data={{ path: f.path, findings: all.filter((x) => x.path === f.path) }} /></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {view === "gaps" && (
        <div className="card">
          <h3><Term k="gap">Untested code</Term> that many places call</h3>
          <p className="hint" style={{ marginTop: -6 }}>A bug here spreads to every caller and no test would catch it. One test for each of the top few is the cheapest safety you can add. Agents get this list from <code>devmem gaps</code>.</p>
          {!d.gaps.length ? <Empty>Every function that other code calls is reached by a test.</Empty> : d.gaps.map((g: any) => (
            <div className="hy-file" key={g.id}>
              <span className="mono" title={g.doc || undefined}>{g.id.split("#")[1]} <span className="muted">{g.file}:{g.line}</span></span>
              <span className="muted">{g.callers} callers in {g.files} file{g.files === 1 ? "" : "s"}</span>
              <EditorLink href={editorHref(repos.data, repo, g.file, g.line)} />
              <CopyPrompt repo={repo} kind="test" data={{ id: g.id }} label="Copy test prompt" />
            </div>
          ))}
        </div>
      )}

      {view === "dupes" && (
        <div className="card">
          {!d.clones.length ? <Empty>{d.local ? "No duplicated blocks found." : "Run the checks to look for duplicated code."}</Empty> : (
            <table><thead><tr><th>First copy</th><th>Second copy</th><th className="num">Size (tokens)</th><th></th></tr></thead><tbody>
              {d.clones.map((c: any, k: number) => <tr key={k}><td className="mono">{c.a}:{c.a_lines.join("-")}</td><td className="mono">{c.b}:{c.b_lines.join("-")}</td><td className="num">{c.tokens}</td>
                <td><div className="row actions"><CopyPrompt repo={repo} kind="duplicate" data={c} label="Copy merge prompt" /></div></td></tr>)}
            </tbody></table>
          )}
        </div>
      )}

      {view === "sources" && (
        <div className="card">
          <div className="row" style={{ marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>Measured test coverage</h3>
            {cov.data?.available ? <Badge kind={cov.data.per_test ? "good" : "warn"}>{cov.data.per_test ? "per-test data" : "lines only"}</Badge> : <Badge>not imported</Badge>}
            <span className="grow" />
            <label className="btn-file"><input type="file" hidden accept=".json,.xml,.info,.lcov" disabled={!!busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) sendCoverage(f); e.target.value = ""; }} /><span className="btnlike"><Icon name="upload" />{busy === "cov" ? "Importing…" : "Import a coverage report"}</span></label>
          </div>
          {cov.data?.available
            ? <p className="hint" style={{ margin: 0 }}>{cov.data.format} report <code>{cov.data.source}</code> from {String(cov.data.ts).slice(0, 16).replace("T", " ")}: {cov.data.stats.covered} of {cov.data.stats.functions} functions ran in tests.
                {cov.data.per_test ? " Test selection (`devmem tests-for`) and “untested” use it, including tests that reach code through fixtures." : " Without per-test data, “untested” uses it but test selection still uses the call graph."}</p>
            : <p className="hint" style={{ margin: 0 }}>Test selection is inferred from calls, so tests that reach code through fixtures or helpers can be missed. Import what your test run already produces: coverage.py JSON (<code>pytest --cov --cov-context=test</code> then <code>coverage json --show-contexts</code>), lcov, Cobertura or JaCoCo XML. A report left in the repo (<code>coverage.json</code>, <code>lcov.info</code>, <code>jacoco.xml</code>) is picked up on every re-index.</p>}
        </div>
      )}
      {view === "sources" && (
        <div className="hy-split">
          <div className="card">
            <div className="row" style={{ marginBottom: 12 }}><h3 style={{ margin: 0 }}>devmem checks</h3>{d.local && <span className="muted">last run {d.local.ts.slice(0, 16).replace("T", " ")}</span>}</div>
            {!d.local ? <Empty>Not run yet. Use Run checks above.</Empty> : (
              <table><thead><tr><th>Check</th><th>Tool</th><th className="num">Findings</th><th>Status</th></tr></thead><tbody>
                {TOOL_JOB.filter(([t]) => (d.local.tools[t] ?? "-") !== "-").map(([t, job]) => {
                  const n = d.local.counts[t] || 0, before = d.local.prev_counts?.[t], dl = before === undefined || before === n ? null : n - before, st = d.local.tools[t];
                  return <tr key={t}><td>{job}</td><td className="mono">{t}</td><td className="num">{n}{dl !== null && <em className={`delta ${dl > 0 ? "up" : "down"}`}>{dl > 0 ? "+" : ""}{dl}</em>}</td><td>{st === "ok" ? <Badge kind="good">ran</Badge> : <Badge kind="warn">{String(st)}</Badge>}</td></tr>;
                })}
              </tbody></table>
            )}
            {d.trend.length > 1 && <div style={{ marginTop: 18 }}><LineChart points={d.trend.map((t: any) => ({ x: t.ts.slice(0, 10), y: t.total }))} label="Findings from devmem checks, per run" height={150} /></div>}
          </div>
          <div className="card">
            <div className="row" style={{ marginBottom: 12 }}><h3 style={{ margin: 0 }}>SonarQube</h3>{sq && <span className="muted">synced {sq.ts.slice(0, 16).replace("T", " ")}</span>}</div>
            {!sq ? <Empty>{canSync ? "Connected, not synced yet. Use Read from SonarQube above." : <>Not connected. <a href="#/integrations">Connect SonarQube</a> to bring in its issues, quality gate and coverage.</>}</Empty> : (
              <>
                <div className="kv"><span>Project</span><span className="mono">{sq.project}{sq.branch ? ` (${sq.branch})` : ""}</span>
                  <span>Server</span><span>{sq.url.replace(/^https?:\/\//, "")}, version {sq.version}</span>
                  <span>Read</span><span>{sq.issues.toLocaleString()} open issues{sq.truncated ? ` (the most severe of ${sq.total_issues.toLocaleString()}; SonarQube returns at most 10,000)` : ""}, {sq.hotspots} security hotspots</span>
                  {sq.matched !== null && sq.matched !== undefined && <><span>Matched</span><span>{sq.matched.toLocaleString()} are in files devmem indexed</span></>}
                  {m.reliability_rating && <><span>Ratings</span><span>reliability {RATING[Math.round(Number(m.reliability_rating))] || "-"}, security {RATING[Math.round(Number(m.security_rating))] || "-"}, maintainability {RATING[Math.round(Number(m.sqale_rating))] || "-"}</span></>}
                  {m.ncloc && <><span>Size</span><span>{Number(m.ncloc).toLocaleString()} lines of code</span></>}
                </div>
                {sq.notes.map((n: string) => <p className="hint" key={n}>Note: {n}</p>)}
                <div className="row" style={{ marginTop: 14 }}><a className="btn-link" href={sq.dashboard} target="_blank" rel="noreferrer">Open the project in SonarQube</a><a className="btn-link" href="#/integrations">Change the connection</a></div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
