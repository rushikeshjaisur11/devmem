import { useEffect, useState } from "react";
import { useRef } from "react";
import { api, upload } from "../api";
import { Badge, Empty, Err, fmtBytes, Icon, Menu, Term, useConfig, useRepos } from "../components";
import { Picked, pickable, readDropped, zipFiles } from "../zip";

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
interface Chosen { label: string; detail: string; name: string; archive?: File; files?: Picked[] }

/** Upload a folder or an archive: the way to onboard a repo when devmem runs on another machine than the code. */
function UploadBox({ maxMb, onDone, onError }: { maxMb: number; onDone: (r: any) => void; onError: (m: string) => void }) {
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const [name, setName] = useState("");
  const [over, setOver] = useState(false);
  const [stage, setStage] = useState<"" | "pack" | "send" | "index">("");
  const [frac, setFrac] = useState(0);
  const folderRef = useRef<HTMLInputElement>(null);
  const zipRef = useRef<HTMLInputElement>(null);
  useEffect(() => { folderRef.current?.setAttribute("webkitdirectory", ""); }, []);

  const takeFolder = (all: Picked[]) => {
    if (!all.length) return;
    const { keep, skipped } = pickable(all);
    if (!keep.length) return onError("That folder has no files to index (only dependencies or very large files).");
    const top = keep[0].path.split("/")[0];
    const size = keep.reduce((n, f) => n + f.file.size, 0);
    setChosen({ label: top, name: top, files: keep, detail: `${keep.length.toLocaleString()} files, ${fmtBytes(size)}${skipped ? ` (${skipped.toLocaleString()} left out: dependencies, version control, files over 5 MB)` : ""}` });
    setName(top); onError("");
  };
  const takeArchive = (f: File) => {
    if (!/\.(zip|tar|tgz|tar\.gz|tar\.bz2|tar\.xz)$/i.test(f.name)) return onError("Choose a .zip or .tar.gz file, or pick the folder itself.");
    const base = f.name.replace(/\.(zip|tar\.gz|tgz|tar\.bz2|tar\.xz|tar)$/i, "");
    setChosen({ label: f.name, name: base, archive: f, detail: fmtBytes(f.size) }); setName(base); onError("");
  };
  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault(); setOver(false);
    const one = e.dataTransfer.files.length === 1 ? e.dataTransfer.files[0] : null;
    if (one && /\.(zip|tar|tgz|gz|bz2|xz)$/i.test(one.name)) return takeArchive(one);
    try { takeFolder(await readDropped(e.dataTransfer.items)); } catch { onError("Could not read the dropped folder. Use Choose folder instead."); }
  };
  const send = async () => {
    if (!chosen) return;
    onError("");
    try {
      let body: Blob = chosen.archive as Blob;
      let filename = chosen.archive?.name || `${chosen.name}.zip`;
      if (chosen.files) { setStage("pack"); setFrac(0); body = await zipFiles(chosen.files, setFrac); filename = `${chosen.name}.zip`; }
      if (body.size > maxMb * 1024 * 1024) throw new Error(`This is ${fmtBytes(body.size)}; the server accepts up to ${maxMb} MB. Upload only the source code, or use a Git URL.`);
      setStage("send"); setFrac(0);
      const r = await upload(`/api/repos/upload?name=${encodeURIComponent(name.trim())}&filename=${encodeURIComponent(filename)}`, body, (f) => { setFrac(f); if (f >= 1) setStage("index"); });
      setChosen(null); setName(""); onDone(r);
    } catch (e: any) { onError(e.message); }
    setStage("");
  };
  const busy = stage !== "";
  return (
    <div>
      <div className={`drop ${over ? "over" : ""} ${chosen ? "has" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
        <input ref={folderRef} type="file" multiple hidden onChange={(e) => { takeFolder(Array.from(e.target.files || []).map((f) => ({ path: (f as any).webkitRelativePath || f.name, file: f }))); e.target.value = ""; }} />
        <input ref={zipRef} type="file" hidden accept=".zip,.tar,.tgz,.gz,.bz2,.xz" onChange={(e) => { const f = e.target.files?.[0]; if (f) takeArchive(f); e.target.value = ""; }} />
        {chosen ? (
          <div className="drop-file">
            <span className="drop-ico"><Icon name={chosen.files ? "folder" : "file"} /></span>
            <div className="grow"><b>{chosen.label}</b><div className="muted">{chosen.detail}</div></div>
            <button className="ghost" disabled={busy} onClick={() => setChosen(null)}>Choose another</button>
          </div>
        ) : (
          <>
            <span className="drop-ico"><Icon name="upload" /></span>
            <div className="drop-title">Drop a project folder or a .zip here</div>
            <div className="muted">or pick it from your computer. Dependencies and <code>.git</code> are left out; up to {maxMb} MB.</div>
            <div className="row" style={{ justifyContent: "center", marginTop: 14 }}>
              <button onClick={() => folderRef.current?.click()}><Icon name="folder" />Choose folder</button>
              <button onClick={() => zipRef.current?.click()}><Icon name="file" />Choose .zip or .tar.gz</button>
            </div>
          </>
        )}
      </div>
      {chosen && (
        <div className="row" style={{ marginTop: 12 }}>
          <label className="field inline grow">Repo name<input value={name} onChange={(e) => setName(e.target.value)} aria-label="Repo name" disabled={busy} /></label>
          <button className="primary" disabled={busy || !name.trim()} onClick={send}><Icon name="upload" />{stage === "pack" ? "Packing…" : stage === "send" ? "Uploading…" : stage === "index" ? "Indexing…" : "Upload and index"}</button>
        </div>
      )}
      {busy && (
        <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(frac * 100)} aria-label="Upload progress">
          <div className={stage === "index" ? "bar busy" : "bar"} style={{ width: stage === "index" ? "100%" : `${Math.round(frac * 100)}%` }} />
          <span>{stage === "pack" ? `Packing the folder, ${Math.round(frac * 100)}%` : stage === "send" ? `Uploading, ${Math.round(frac * 100)}%` : "Uploaded. Indexing the code; a large repo takes a minute or two."}</span>
        </div>
      )}
    </div>
  );
}

export default function Repos(_: any) {
  const { data, error, loading, reload } = useRepos();
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
        {src === "upload" && <UploadBox maxMb={cfg?.max_upload_mb || 300} onError={setErr} onDone={(r) => { setMsg(`Onboarded ${r.name}: ${r.symbols} symbols in ${r.files} files`); reload(); }} />}
        {src === "git" && (
          <>
            <div className="row">
              <input className="grow" placeholder="https://github.com/org/repo.git  or  git@github.com:org/repo.git" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Git URL" />
              <input placeholder="Branch (default)" value={branch} onChange={(e) => setBranch(e.target.value)} aria-label="Branch" style={{ width: 150 }} />
              <input placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Repo name" style={{ width: 160 }} />
              <button className="primary" disabled={!url || busy === "add"} onClick={() => act("add", () => api.post("/api/repos", { url, branch: branch || undefined, name: name || undefined }), (r) => `Onboarded ${r.name}: ${r.symbols} symbols`)}>
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
              <button className="primary" disabled={!path || busy === "add"} onClick={() => act("add", () => api.post("/api/repos", { path, name: name || undefined }), (r) => `Onboarded ${r.name}: ${r.symbols} symbols`)}>
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
                  <td>{r.uploaded ? <span className="muted">-</span> : <BranchPicker repo={r} disabled={!!busy} onSwitch={(b) => confirm(`Check out ${b} in ${r.path} and re-index it?`) ? act("b" + r.name, () => api.post(`/api/repos/${r.name}/branch`, { branch: b }), (x) => `${r.name}: now on ${x.branch}, ${x.symbols} symbols (parsed ${x.stats.parsed}, reused ${x.stats.reused})`) : reload()} />}</td>
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
                      <button className="primary sm" disabled={!!busy || !r.indexed} title="Write .devmem/ and the AGENTS.md block that agents read" onClick={() => act("p" + r.name, () => api.post(`/api/repos/${r.name}/publish`, { commit: commit && !r.uploaded }), (x) => `${r.name}: wrote ${x.written.length} files${x.branch ? ` on branch ${x.branch}` : ""}${r.uploaded ? ". Download the knowledge from the row menu and unpack it in your repo." : ""}`)}><Icon name="send" />{busy === "p" + r.name ? "Publishing…" : "Publish"}</button>
                      <button className="icon-btn bordered" disabled={!!busy} aria-label={`Re-index ${r.name}`} title="Re-index: read the files again" onClick={() => act("i" + r.name, () => api.post(`/api/repos/${r.name}/index`), (x) => `${r.name}: parsed ${x.stats.parsed}, reused ${x.stats.reused}`)}><span className={busy === "i" + r.name ? "spin" : ""}><Icon name="refresh" /></span></button>
                      <Menu disabled={!!busy} label={`More actions for ${r.name}`} items={[
                        { label: busy === "h" + r.name ? "Running…" : "Run code health", icon: "shield", hint: "lint, dead code, complexity", disabled: !r.indexed, onClick: () => act("h" + r.name, () => api.post(`/api/repos/${r.name}/hygiene`), (x) => `${r.name} code health: ${JSON.stringify(x.counts)}`) },
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
