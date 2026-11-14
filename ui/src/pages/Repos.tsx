import { useEffect, useState } from "react";
import { api, job } from "../api";
import { Badge, Empty, Err, Icon, Menu, Term, useConfig, useRepos } from "../components";
import { UploadBox } from "../UploadBox";

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

type Src = "upload" | "git" | "path";

export default function Repos(_: any) {
  const { data, error, loading, reload } = useRepos();
  const working = !!data?.some((r: any) => r.working);
  useEffect(() => { if (!working) return; const t = setInterval(reload, 2500); return () => clearInterval(t); }, [working, reload]);
  const cfg = useConfig().data;
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [src, setSrc] = useState<Src>("upload");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [name, setName] = useState("");
  const [commit, setCommit] = useState(false);
  const pathsOk = cfg ? cfg.paths_allowed : true;

  async function act(key: string, fn: () => Promise<any>, ok: (r: any) => string) {
    setBusy(key); setErr(""); setMsg("");
    try { setMsg(ok(await fn())); reload(); } catch (e: any) { setErr(e.message); }
    setBusy("");
  }
  const tab = (id: Src, icon: string, label: string) => (
    <button role="tab" aria-selected={src === id} className={src === id ? "on" : ""} onClick={() => { setSrc(id); setErr(""); }}><Icon name={icon} />{label}</button>
  );

  return (
    <>
      <h1>Repos</h1>
      <p className="sub">Add a repo to index its code. Publish writes the map agents read (<code>.devmem/</code> and an <code>AGENTS.md</code> block) into the repo; nothing else is changed.</p>
      <div className="card">
        <h3>Add a repo</h3>
        <div className="seg big" role="tablist" style={{ marginBottom: 16 }}>
          {tab("upload", "upload", "Upload")}
          {tab("git", "git", "Git URL")}
          {pathsOk && tab("path", "folder", cfg?.deployed ? "Path on the server" : "Path on this computer")}
        </div>
        {src === "upload" && <UploadBox maxMb={cfg?.max_upload_mb || 300} endpoint={(n, f) => `/api/repos/upload?name=${encodeURIComponent(n)}&filename=${encodeURIComponent(f)}`} onError={setErr} onDone={(r) => { setMsg(`Onboarded ${r.name}: ${r.symbols} symbols in ${r.files} files`); reload(); }} />}
        {src === "git" && (
          <>
            <div className="row">
              <input className="grow" placeholder="https://github.com/org/repo.git  or  git@github.com:org/repo.git" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Git URL" />
              <input placeholder="Branch (default)" value={branch} onChange={(e) => setBranch(e.target.value)} aria-label="Branch" style={{ width: 150 }} />
              <input placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Repo name" style={{ width: 160 }} />
              <button className="primary" disabled={!url || busy === "add"} onClick={() => act("add", () => job("/api/repos", { url, branch: branch || undefined, name: name || undefined }), (r) => `Onboarded ${r.name}: ${r.symbols} symbols`)}>
                <Icon name="git" />{busy === "add" ? "Cloning and indexing…" : "Clone and index"}
              </button>
            </div>
            <p className="hint" style={{ margin: "10px 0 0" }}>Cloned into <code>~/.devmem/clones/&lt;name&gt;</code> on {cfg?.deployed ? "the server, with the server's" : "this machine, with its"} git credentials (SSH keys or a credential helper); nothing is prompted. Pick another branch later from the table.</p>
          </>
        )}
        {src === "path" && pathsOk && (
          <>
            <div className="row">
              <input className="grow" placeholder={cfg?.deployed ? "Path on the server, e.g. /srv/repos/orders-etl" : "Path to a local clone, e.g. C:\\work\\orders-etl"} value={path} onChange={(e) => setPath(e.target.value)} aria-label="Repo path" />
              <input placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Repo name" style={{ width: 160 }} />
              <button className="primary" disabled={!path || busy === "add"} onClick={() => act("add", () => job("/api/repos", { path, name: name || undefined }), (r) => `Onboarded ${r.name}: ${r.symbols} symbols`)}>
                <Icon name="plus" />{busy === "add" ? "Indexing…" : "Add and index"}
              </button>
            </div>
            <p className="hint" style={{ margin: "10px 0 0" }}>Indexed in place, so it follows your commits and branch switches. The folder must be on the machine that runs devmem.</p>
          </>
        )}
      </div>
      <Err msg={error || err} />
      {msg && <div className="ok">{msg}</div>}
      <div className="card">
        {loading ? <Empty>Loading…</Empty> : !data?.length ? <Empty>No repos yet. Upload one above.</Empty> : (
          <table>
            <thead><tr><th>Repo</th><th>Branch</th><th><Term k="index">Status</Term></th><th className="num">Symbols</th><th>Languages</th><th>Code health</th><th></th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.name}>
                  <td className="repo-name"><b>{r.name}</b>{r.uploaded ? <div className="path">uploaded copy</div> : <div className="path" title={r.path}>{r.path}</div>}</td>
                  <td>{r.uploaded ? <span className="muted">-</span> : <BranchPicker repo={r} disabled={!!busy} onSwitch={(b) => confirm(`Check out ${b} in ${r.path} and re-index it?`) ? act("b" + r.name, () => job(`/api/repos/${r.name}/branch`, { branch: b }), (x) => `${r.name}: now on ${x.branch}, ${x.symbols} symbols (parsed ${x.stats.parsed}, reused ${x.stats.reused})`) : reload()} />}</td>
                  <td><div className="tags">
                    {r.working && <Badge kind="warn">{({ add: "indexing", index: "re-indexing", branch: "switching branch", publish: "publishing", hygiene: "running checks", sonar: "reading SonarQube", modernize: "scanning", selfcheck: "measuring" } as Record<string, string>)[r.working.kind] || "working"}… {r.working.seconds}s</Badge>}
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
                      <button className="primary sm" disabled={!!busy || !r.indexed} title="Write .devmem/ and the AGENTS.md block that agents read" onClick={() => act("p" + r.name, () => job(`/api/repos/${r.name}/publish`, { commit: commit && !r.uploaded }), (x) => `${r.name}: wrote ${x.written.length} files${x.branch ? ` on branch ${x.branch}` : ""}${r.uploaded ? ". Download the knowledge from the row menu and unpack it in your repo." : ""}`)}><Icon name="send" />{busy === "p" + r.name ? "Publishing…" : "Publish"}</button>
                      <button className="icon-btn bordered" disabled={!!busy} aria-label={`Re-index ${r.name}`} title="Re-index: read the files again" onClick={() => act("i" + r.name, () => job(`/api/repos/${r.name}/index`), (x) => `${r.name}: parsed ${x.stats.parsed}, reused ${x.stats.reused}`)}><span className={busy === "i" + r.name ? "spin" : ""}><Icon name="refresh" /></span></button>
                      <Menu disabled={!!busy} label={`More actions for ${r.name}`} items={[
                        { label: busy === "h" + r.name ? "Running…" : "Run code health", icon: "shield", hint: "lint, dead code, complexity", disabled: !r.indexed, onClick: () => act("h" + r.name, () => job(`/api/repos/${r.name}/hygiene`), (x) => `${r.name} code health: ${JSON.stringify(x.counts)}`) },
                        ...(r.indexed ? [{ label: "AI analysis", icon: "spark", hint: "descriptions, module summaries, overview", href: `#/analysis?repo=${r.name}` }] : []),
                        ...(r.indexed && r.knowledge_problems[0] !== "not published" ? [{ label: "Download knowledge (.zip)", icon: "download", hint: "unpack at the root of your repo", href: `/api/repos/${r.name}/knowledge.zip` }] : []),
                        { label: "Remove", icon: "trash", danger: true, hint: r.uploaded ? "deletes the uploaded copy" : "your files stay untouched", onClick: () => { if (confirm(r.uploaded ? `Remove ${r.name} and delete its uploaded copy?` : `Remove ${r.name} from devmem? (files stay untouched)`)) act("d" + r.name, () => api.del(`/api/repos/${r.name}`), () => `Removed ${r.name}`); } },
                      ]} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!!data?.some((r) => !r.uploaded) && <label className="row" style={{ marginTop: 14 }}><input type="checkbox" checked={commit} onChange={(e) => setCommit(e.target.checked)} /> Publish also commits on branch <code>devmem/knowledge-sync</code> (you push and open the PR)</label>}
      </div>
    </>
  );
}
