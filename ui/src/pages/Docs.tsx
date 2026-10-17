import { useState } from "react";
import { api, q } from "../api";
import { Badge, Empty, Err, RepoSelect, useAsync, useRepos } from "../components";

export default function Docs(_: any) {
  const libs = useAsync(() => api.get("/api/docs"), []);
  const repos = useRepos();
  const [spec, setSpec] = useState("");
  const [follow, setFollow] = useState(true);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [repo, setRepo] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<any[] | null>(null);

  async function run(key: string, fn: () => Promise<any>, ok: (r: any) => string) {
    setBusy(key); setErr(""); setMsg("");
    try { setMsg(ok(await fn())); libs.reload(); } catch (e: any) { setErr(e.message); }
    setBusy("");
  }
  async function search() {
    setErr("");
    try { setHits(await api.get(`/api/docs/search?${q({ q: query, repo })}`)); } catch (e: any) { setErr(e.message); }
  }
  const list: any[] = libs.data || [];
  return (
    <>
      <h2>Library docs</h2>
      <p className="sub">Agents without internet cannot look anything up. Fetch the current documentation here, where there is internet, and it travels to them inside the hub and the repos that use each library. Newer than the org's older code, so agents stop copying outdated patterns.</p>
      <Err msg={libs.error || err} />
      {msg && <div className="ok">{msg}</div>}
      <div className="card">
        <h3>Add documentation</h3>
        <div className="row">
          <input className="grow" placeholder="https://docs.example.dev/llms.txt  ·  a docs page URL  ·  pypi:pydantic  ·  npm:zod" value={spec} onChange={(e) => setSpec(e.target.value)} aria-label="Docs source" />
          <label title="For llms.txt: also fetch the pages it links to"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />Follow links</label>
          <button className="primary" disabled={!spec.trim() || busy === "add"} onClick={() => run("add", () => api.post("/api/docs", { spec, follow }), (r) => `${r.name} ${r.version || ""}: ${r.pages} pages, ${Math.round(r.bytes / 1024)} KB`)}>{busy === "add" ? "Fetching…" : "Fetch docs"}</button>
        </div>
        <p className="muted" style={{ margin: "10px 0 0", fontSize: 12.5 }}>Prefer an <code>llms.txt</code> / <code>llms-full.txt</code> when the library publishes one (try <code>https://&lt;docs-site&gt;/llms.txt</code>). <code>pypi:</code> and <code>npm:</code> pull the package page and, if the project's docs site has an llms.txt, that too.</p>
      </div>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>Libraries on file · {list.length}</h3>
          <button disabled={busy === "refresh" || !list.length} onClick={() => run("refresh", () => api.post("/api/docs/refresh"), (r) => Object.entries(r).map(([k, v]) => `${k}: ${v}`).join(" · "))}>{busy === "refresh" ? "Refreshing…" : "Refresh all"}</button>
        </div>
        {!list.length ? <Empty>No docs yet. Add a library above.</Empty> : (
          <table style={{ marginTop: 8 }}>
            <thead><tr><th>Library</th><th>Version</th><th>Fetched</th><th className="num">Pages</th><th>Source</th><th></th></tr></thead>
            <tbody>{list.map((l) => (
              <tr key={l.name}>
                <td><b>{l.name}</b></td><td>{l.version || "-"}</td>
                <td>{l.fetched} {l.age_days > 120 ? <Badge kind="warn">{l.age_days}d old</Badge> : <Badge kind="good">fresh</Badge>}</td>
                <td className="num">{l.pages}</td><td className="mono muted" style={{ overflowWrap: "anywhere" }}>{l.source}</td>
                <td><button className="danger" onClick={() => confirm(`Remove ${l.name} docs?`) && run("rm" + l.name, () => api.del(`/api/docs/${l.name}`), () => `Removed ${l.name}`)}>Remove</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      <div className="card">
        <h3>See what an agent would find</h3>
        <div className="row">
          <span className="muted">In repo</span>{repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}
          <input className="grow" placeholder="e.g. validate an email address" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && query.trim() && repo && search()} aria-label="Docs query" />
          <button disabled={!query.trim() || !repo} onClick={search}>Search</button>
        </div>
        <p className="muted" style={{ margin: "8px 0 0", fontSize: 12.5 }}>Searches the docs <i>inside that repo</i> (<code>.devmem/docs</code>, copied there by Publish), exactly as <code>devmem docs</code> does for the agent.</p>
        {hits && (hits.length === 0 ? <Empty>Nothing on file in that repo matches. Publish the repo after adding docs, or add the library.</Empty> :
          hits.map((h, i) => (
            <div key={i} style={{ marginTop: 14 }}>
              <div><b>{h.heading || h.title}</b> <Badge>{h.lib} {h.version}</Badge> <span className="muted">fetched {h.fetched}</span> <a href={h.url} target="_blank" rel="noreferrer">source</a></div>
              <pre className="code" style={{ margin: "6px 0 0", padding: 10, whiteSpace: "pre-wrap" }}>{h.text.slice(0, 900)}</pre>
            </div>
          )))}
      </div>
    </>
  );
}
