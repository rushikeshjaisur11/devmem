import { useState } from "react";
import { api } from "../api";
import { Badge, Empty, Err, useRepos } from "../components";
import { nav } from "../route";

export default function Repos(_: any) {
  const { data, error, loading, reload } = useRepos();
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [commit, setCommit] = useState(false);

  async function act(key: string, fn: () => Promise<any>, ok: (r: any) => string) {
    setBusy(key); setErr(""); setMsg("");
    try { setMsg(ok(await fn())); reload(); } catch (e: any) { setErr(e.message); }
    setBusy("");
  }

  return (
    <>
      <h2>Repos</h2>
      <p className="sub">Onboard local clones, keep their index fresh, and publish knowledge into each repo.</p>
      <div className="card">
        <h3>Onboard a repo</h3>
        <div className="row">
          <input className="grow" placeholder="Path to a local clone, e.g. C:\work\orders-etl" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Repo path" />
          <input placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Repo name" />
          <button className="primary" disabled={!path || busy === "add"} onClick={() => act("add", () => api.post("/api/repos", { path, name: name || undefined }), (r) => `Onboarded ${r.name}: ${r.symbols} symbols`)}>
            {busy === "add" ? "Indexing…" : "Add and index"}
          </button>
        </div>
      </div>
      <Err msg={error || err} />
      {msg && <div className="ok">{msg}</div>}
      <div className="card">
        {loading ? <Empty>Loading…</Empty> : !data?.length ? <Empty>No repos yet. Add one above.</Empty> : (
          <table>
            <thead><tr><th>Repo</th><th className="num">Symbols</th><th className="num">Files</th><th>Languages</th><th>Index</th><th>Knowledge</th><th>Hygiene</th><th></th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.name}>
                  <td><a onClick={() => nav("explorer", { repo: r.name })}>{r.name}</a><div className="muted mono" title={r.path} style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis" }}>{r.path}</div></td>
                  <td className="num">{r.symbols ?? "-"}</td><td className="num">{r.files ?? "-"}</td>
                  <td>{r.langs ? Object.entries(r.langs).map(([l, n]) => `${l} ${n}`).join(", ") : "-"}</td>
                  <td>{!r.indexed ? <Badge kind="bad">not indexed</Badge> : r.stale ? <Badge kind="warn">stale: HEAD moved</Badge> : <Badge kind="good">fresh</Badge>}</td>
                  <td>{!r.indexed ? "-" : r.knowledge_problems.length === 0 ? <Badge kind="good">published</Badge> :
                    r.knowledge_problems[0] === "not published" ? <Badge>not published</Badge> :
                    <Badge kind="warn"><span title={r.knowledge_problems.join("\n")}>{r.knowledge_problems.length} issue(s)</span></Badge>}</td>
                  <td>{r.hygiene ? Object.entries(r.hygiene).map(([k, v]) => `${k} ${v}`).join(" · ") : <span className="muted">not run</span>}</td>
                  <td>
                    <div className="row actions">
                      <button disabled={!!busy} onClick={() => act("i" + r.name, () => api.post(`/api/repos/${r.name}/index`), (x) => `${r.name}: parsed ${x.stats.parsed}, reused ${x.stats.reused}`)}>{busy === "i" + r.name ? "…" : "Re-index"}</button>
                      <button disabled={!!busy || !r.indexed} onClick={() => act("p" + r.name, () => api.post(`/api/repos/${r.name}/publish`, { commit }), (x) => `${r.name}: wrote ${x.written.length} files${x.branch ? ` on branch ${x.branch}` : ""}`)}>{busy === "p" + r.name ? "…" : "Publish"}</button>
                      <button disabled={!!busy || !r.indexed} onClick={() => act("h" + r.name, () => api.post(`/api/repos/${r.name}/hygiene`), (x) => `${r.name} hygiene: ${JSON.stringify(x.counts)}`)}>{busy === "h" + r.name ? "Running…" : "Hygiene"}</button>
                      <button className="danger" disabled={!!busy} onClick={() => confirm(`Remove ${r.name} from devmem? (files stay untouched)`) && act("d" + r.name, () => api.del(`/api/repos/${r.name}`), () => `Removed ${r.name}`)}>Remove</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <label className="row" style={{ marginTop: 10 }}><input type="checkbox" checked={commit} onChange={(e) => setCommit(e.target.checked)} /> Publish also commits on branch <code>devmem/knowledge-sync</code> (you push and open the PR)</label>
      </div>
    </>
  );
}
