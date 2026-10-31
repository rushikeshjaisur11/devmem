import { useState } from "react";
import { api } from "../api";
import { Badge, Empty, Err, useAsync, useRepos } from "../components";
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
        Every session: <mark>{t.always == null ? "no AGENTS.md block" : `${n(t.always)} tokens`}</mark>
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

export default function Overview(_: any) {
  const repos = useRepos();
  const mem = useAsync(() => api.get("/api/memory"), []);
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
