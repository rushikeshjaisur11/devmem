import { useState } from "react";
import { api } from "../api";
import { Badge, Empty, Err, Term, useAsync, useRepos } from "../components";
import { nav } from "../route";

const HUES = ["--accent", "--c-class", "--good", "--warn", "--accent-2", "--bad", "--text-3"];
const n = (x: number | null | undefined) => (x ?? 0).toLocaleString();

function Footprint({ t }: { t: any }) {
  const files = Object.entries(t.files as Record<string, number | null>).filter(([, v]) => v).sort((a, b) => (b[1] as number) - (a[1] as number)) as [string, number][];
  const total = files.reduce((s, [, v]) => s + v, 0);
  const over = (f: string, v: number) => (f === "map.md" ? v > t.map_budget + 200 : v > 25000);
  if (!files.length) return <p className="muted">Not published yet: agents in this repo have no map. Publish it to write <code>.devmem/knowledge</code>.</p>;
  return (
    <>
      <p className="budget">
        <Term k="session">Every session</Term>: <mark>{t.always == null ? "no AGENTS.md block" : `${n(t.always)} tokens`}</mark>
        {t.always != null && t.always > t.always_budget && <Badge kind="warn">over the {t.always_budget}-token budget: re-publish</Badge>}
        <span className="muted"> · then only the topic files a task needs, {n(total)} tokens if an agent read them all</span>
      </p>
      <div className="strip" role="img" aria-label={files.map(([f, v]) => `${f} ${v} tokens`).join(", ")}>
        {files.map(([f, v], i) => <span key={f} title={`${f}: ${n(v)} tokens`} style={{ width: `${(v / total) * 100}%`, background: `var(${HUES[i % HUES.length]})` }} />)}
      </div>
      <div className="strip-key">
        {files.map(([f, v], i) => <div key={f}><i style={{ background: `var(${HUES[i % HUES.length]})` }} />{f.replace(/\.md$/, "")}<span className={`t ${over(f, v) ? "over" : ""}`} title={over(f, v) ? "over budget" : undefined}>{n(v)}</span></div>)}
      </div>
    </>
  );
}

/** First-run guide: each step is ticked from real data, so it doubles as a health check. */
function GettingStarted({ repos, mem, docs }: { repos: any[]; mem: any; docs: any[] }) {
  const indexed = repos.filter((r) => r.indexed);
  const ruleOrLesson = mem && Object.values(mem.repos).some((x: any) => x.lessons.length || x.rules.some((r: any) => !String(r.id).startsWith("rules.md#"))) || (mem?.hub_rules || []).length > 0;
  const steps: [boolean, string, any, string, string][] = [
    [repos.length > 0, "Add a repo", "devmem indexes its functions, calls, tests and tables. Nothing in the repo changes.", "repos", "Add a repo"],
    [indexed.some((r) => !r.knowledge_problems.includes("not published")), "Publish its map", <>Writes the short <Term k="knowledge">knowledge files</Term> and <code>AGENTS.md</code> block agents read instead of whole files.</>, "repos", "Publish"],
    [repos.some((r) => (r.connected || []).length > 0), "Connect your coding agent", <>Registers devmem as an <Term k="mcp">MCP server</Term> for Claude Code, Cursor, Copilot, Codex, Gemini and more.</>, "agents", "Connect"],
    [docs.length > 0, "Add docs for your libraries", "Agents then write against the current API instead of copying older patterns.", "docs", "Add docs"],
    [!!ruleOrLesson, "Add a rule or lesson", "Standing instructions (\"never call the payments API from tests\") that agents recall on every task.", "memory", "Add a rule"],
    [repos.some((r) => r.usage?.calls > 0), "Watch an agent use it", "Run a task with your agent; its devmem queries and token savings show up here.", "agents", "See activity"],
  ];
  const done = steps.filter((s) => s[0]).length;
  if (done === steps.length) return null;
  return (
    <section className="card">
      <div className="row" style={{ marginBottom: 6 }}><h3 style={{ margin: 0 }}>Getting started</h3><span className="muted">{done} of {steps.length} done</span></div>
      <div className="checklist">
        {steps.map(([ok, title, why, page, cta], i) => (
          <div key={title} className={`check ${ok ? "done" : ""}`}>
            <span className="dot" aria-hidden="true">{ok ? "✓" : i + 1}</span>
            <div><b>{title}</b>{!ok && <span className="why">{why}</span>}</div>
            {!ok && <a className="chip" href={`#/${page}`}>{cta}</a>}
          </div>
        ))}
      </div>
    </section>
  );
}

export default function Overview(_: any) {
  const repos = useRepos();
  const mem = useAsync(() => api.get("/api/memory"), []);
  const alerts = useAsync(() => api.get("/api/alerts"), []);
  const docs = useAsync(() => api.get("/api/docs"), []);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const list: any[] = repos.data || [];
  const indexed = list.filter((r) => r.indexed);
  const always = indexed.map((r) => r.agent_tokens?.always).filter((x) => x != null);
  const lessons = (r: string) => mem.data?.repos[r]?.lessons.length ?? 0;
  const rules = (r: string) => mem.data?.repos[r]?.rules.length ?? 0;
  const publish = async (name: string) => {
    setBusy(name); setErr(""); setMsg("");
    try { const x = await api.post(`/api/repos/${name}/publish`, {}); setMsg(`Published ${name}: wrote ${x.written.length} files.`); repos.reload(); } catch (e: any) { setErr(e.message); }
    setBusy("");
  };

  return (
    <>
      <h2>Overview</h2>
      {repos.loading ? <p className="sub">Loading…</p> : !list.length ? (
        <div className="card"><Empty>No repos yet. <a href="#/repos">Add a repo</a> to index its code and publish a map your agents can read instead of whole files.</Empty></div>
      ) : (
        <p className="lede">
          {indexed.length} of {list.length} repo{list.length === 1 ? "" : "s"} indexed, <b>{n(indexed.reduce((s, r) => s + (r.symbols || 0), 0))} symbols</b> mapped.{" "}
          {always.length ? <>Agents start each session with <mark>{n(Math.max(...always))} tokens</mark> of devmem instructions and look up the rest on demand.</> : <>Publish a repo so its agents stop reading whole files.</>}
        </p>
      )}
      {(alerts.data || []).length > 0 && <div className="alerts" role="list" aria-label="Needs attention">
        {alerts.data.slice(0, 6).map((a: any, i: number) => <div key={i} role="listitem" className={`alert ${a.level}`}>{a.repo && <b>{a.repo}</b>}{a.text}<a href={`#/${a.page}${a.repo ? `?repo=${encodeURIComponent(a.repo)}` : ""}`}>Fix</a></div>)}
      </div>}
      {repos.data && <GettingStarted repos={list} mem={mem.data} docs={docs.data || []} />}
      <Err msg={repos.error || err} />
      {msg && <div className="ok">{msg}</div>}
      {indexed.map((r) => (
        <section className="card" key={r.name}>
          <div className="repo-head">
            <h3>{r.name}</h3>
            {r.branch && <Badge>{r.branch}</Badge>}
            {r.stale ? <Badge kind="warn">index behind HEAD</Badge> : <Badge kind="good">index fresh</Badge>}
            {r.accuracy === "fast" && <Badge kind="bad">reduced accuracy</Badge>}
            {r.knowledge_problems.length > 0 && r.knowledge_problems[0] !== "not published" && <Badge kind="warn"><span title={r.knowledge_problems.join("\n")}>{r.knowledge_problems.length} knowledge issue{r.knowledge_problems.length === 1 ? "" : "s"}</span></Badge>}
            <span className="path" title={r.path}>{r.path}</span>
            <span className="grow" />
            <button onClick={() => nav("impact", { repo: r.name })}>Check impact</button>
            <button className="primary" disabled={!!busy} onClick={() => publish(r.name)}>{busy === r.name ? "Publishing…" : "Publish"}</button>
          </div>
          <div className="ov-grid">
            <div className="facts">
              <div><b>{n(r.symbols)}</b><span>symbols in {n(r.files)} files</span></div>
              <div><b>{n(r.edges)}</b><span>call, test and table edges</span></div>
              <div><b>{lessons(r.name)} lessons, {rules(r.name)} rules</b><span>agents recall them per task: <a href="#/memory">edit</a></span></div>
            </div>
            <div>
              <Footprint t={r.agent_tokens} />
              {r.usage?.calls > 0 ? <p className="budget" style={{ marginTop: 14 }}>Measured on this machine: <b>{n(r.usage.calls)} devmem queries</b> answered with <mark>{n(r.usage.out_tokens)} tokens</mark> instead of ~{n(r.usage.file_tokens)} tokens of the files they point to{r.usage.file_tokens > 0 && <> ({Math.floor((1 - r.usage.out_tokens / r.usage.file_tokens) * 100)}% less)</>}.</p>
                : <p className="hint" style={{ marginTop: 14 }}>No agent queries logged here yet. Each <code>devmem</code> command or MCP call records its token cost in <code>.devmem/usage.jsonl</code>.</p>}
            </div>
          </div>
        </section>
      ))}
    </>
  );
}
