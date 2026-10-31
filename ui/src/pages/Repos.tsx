import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Empty, Err, useRepos } from "../components";

function BranchPicker({ repo, disabled, onSwitch }: { repo: any; disabled: boolean; onSwitch: (b: string) => void }) {
  const [branches, setBranches] = useState<string[]>([]);
  const [dirty, setDirty] = useState<string[]>([]);
  const load = () => api.get(`/api/repos/${repo.name}/branches`).then((r: any) => { setBranches(r.branches); setDirty(r.dirty); }).catch(() => {});
  useEffect(() => { if (repo.indexed) load(); }, [repo.name, repo.branch]); // eslint-disable-line
  if (!repo.indexed) return <>-</>;
  if (!repo.branch && !branches.length) return <Badge>not a git repo</Badge>;
  const list = repo.branch && !branches.includes(repo.branch) ? [repo.branch, ...branches] : branches;
  return (
    <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
      <select value={repo.branch || ""} disabled={disabled || dirty.length > 0} onFocus={load} aria-label={`Branch of ${repo.name}`}
        title={dirty.length ? `Uncommitted changes (${dirty.slice(0, 5).join(", ")}): commit or stash to switch branch` : "Checks out the branch in the clone and re-indexes it"}
        onChange={(e) => e.target.value && e.target.value !== repo.branch && onSwitch(e.target.value)}>
        {!repo.branch && <option value="">detached HEAD</option>}
        {list.map((b) => <option key={b} value={b}>{b}</option>)}
      </select>
      {dirty.length > 0 && <Badge kind="warn">dirty</Badge>}
    </div>
  );
}

export default function Repos(_: any) {
  const { data, error, loading, reload } = useRepos();
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [src, setSrc] = useState<"path" | "git">("path");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
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
      <p className="sub">Add a repo to index its code. Publish writes the map agents read (<code>.devmem/</code> and an <code>AGENTS.md</code> block) into the repo; nothing else is changed.</p>
      <div className="card">
        <h3>Onboard a repo</h3>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={src === "path"} className={src === "path" ? "on" : ""} onClick={() => setSrc("path")}>Local path</button>
          <button role="tab" aria-selected={src === "git"} className={src === "git" ? "on" : ""} onClick={() => setSrc("git")}>Git URL (clone)</button>
        </div>
        <div className="row">
          {src === "path"
            ? <input className="grow" placeholder="Path to a local clone, e.g. C:\work\orders-etl" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Repo path" />
            : <>
                <input className="grow" placeholder="https://github.com/org/repo.git  or  git@github.com:org/repo.git" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Git URL" />
                <input placeholder="Branch (default)" value={branch} onChange={(e) => setBranch(e.target.value)} aria-label="Branch" style={{ width: 150 }} />
              </>}
          <input placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Repo name" />
          <button className="primary" disabled={(src === "path" ? !path : !url) || busy === "add"}
            onClick={() => act("add", () => api.post("/api/repos", src === "path" ? { path, name: name || undefined } : { url, branch: branch || undefined, name: name || undefined }), (r) => `Onboarded ${r.name}: ${r.symbols} symbols`)}>
            {busy === "add" ? (src === "git" ? "Cloning & indexing…" : "Indexing…") : src === "git" ? "Clone and index" : "Add and index"}
          </button>
        </div>
        {src === "git" && <p className="muted" style={{ margin: "10px 0 0", fontSize: 12.5 }}>Cloned into <code>~/.devmem/clones/&lt;name&gt;</code> using your machine's git credentials (SSH keys or a credential helper); nothing is prompted. Pick another branch later from the table.</p>}
      </div>
      <Err msg={error || err} />
      {msg && <div className="ok">{msg}</div>}
      <div className="card">
        {loading ? <Empty>Loading…</Empty> : !data?.length ? <Empty>No repos yet. Add one above.</Empty> : (
          <table>
            <thead><tr><th>Repo</th><th>Branch</th><th>Status</th><th className="num">Symbols</th><th>Languages</th><th>Hygiene findings</th><th></th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.name}>
                  <td className="repo-name"><b>{r.name}</b><div className="path" title={r.path}>{r.path}</div></td>
                  <td><BranchPicker repo={r} disabled={!!busy} onSwitch={(b) => confirm(`Check out ${b} in ${r.path} and re-index it?`) ? act("b" + r.name, () => api.post(`/api/repos/${r.name}/branch`, { branch: b }), (x) => `${r.name}: now on ${x.branch}, ${x.symbols} symbols (parsed ${x.stats.parsed}, reused ${x.stats.reused})`) : reload()} /></td>
                  <td><div className="tags">
                    {!r.indexed ? <Badge kind="bad">not indexed</Badge> : r.stale ? <Badge kind="warn">index behind HEAD</Badge> : <Badge kind="good">index fresh</Badge>}
                    {r.indexed && (r.knowledge_problems.length === 0 ? <Badge kind="good">published</Badge> :
                      r.knowledge_problems[0] === "not published" ? <Badge>not published</Badge> :
                      <Badge kind="warn"><span title={r.knowledge_problems.join("\n")}>{r.knowledge_problems.length} knowledge issue{r.knowledge_problems.length === 1 ? "" : "s"}</span></Badge>)}
                    {r.indexed && r.accuracy === "fast" && <Badge kind="bad">reduced accuracy</Badge>}
                  </div></td>
                  <td className="num">{r.symbols?.toLocaleString() ?? "-"}<div className="muted" style={{ fontSize: 12.5 }}>{r.files ? `${r.files} files` : ""}</div></td>
                  <td><div className="tags">{r.langs ? Object.entries(r.langs as Record<string, number>).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([l, n]) => <span className="tag" key={l}>{l} <b>{n}</b></span>) : "-"}{r.langs && Object.keys(r.langs).length > 3 && <span className="tag" title={Object.entries(r.langs).slice(3).map(([l, n]) => `${l} ${n}`).join(", ")}>+{Object.keys(r.langs).length - 3}</span>}</div></td>
                  <td>{r.hygiene ? <div className="tags">{Object.entries(r.hygiene).map(([k, v]) => <span className="tag" key={k}>{k} <b>{String(v)}</b></span>)}</div> : <span className="muted">not run</span>}</td>
                  <td>
                    <div className="row actions">
                      <button className="quiet" disabled={!!busy} onClick={() => act("i" + r.name, () => api.post(`/api/repos/${r.name}/index`), (x) => `${r.name}: parsed ${x.stats.parsed}, reused ${x.stats.reused}`)}>{busy === "i" + r.name ? "…" : "Re-index"}</button>
                      <button className="primary" disabled={!!busy || !r.indexed} onClick={() => act("p" + r.name, () => api.post(`/api/repos/${r.name}/publish`, { commit }), (x) => `${r.name}: wrote ${x.written.length} files${x.branch ? ` on branch ${x.branch}` : ""}`)}>{busy === "p" + r.name ? "…" : "Publish"}</button>
                      <button className="quiet" disabled={!!busy || !r.indexed} onClick={() => act("h" + r.name, () => api.post(`/api/repos/${r.name}/hygiene`), (x) => `${r.name} hygiene: ${JSON.stringify(x.counts)}`)}>{busy === "h" + r.name ? "Running…" : "Hygiene"}</button>
                      <button className="quiet danger" disabled={!!busy} onClick={() => confirm(`Remove ${r.name} from devmem? (files stay untouched)`) && act("d" + r.name, () => api.del(`/api/repos/${r.name}`), () => `Removed ${r.name}`)}>Remove</button>
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
