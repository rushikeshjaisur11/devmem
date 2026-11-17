import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, job } from "../api";
import { Badge, Empty, Err, Icon, Menu, Term, useConfig, useRepos } from "../components";
import { UploadBox } from "../UploadBox";

const KIND: Record<string, string> = { add: "Indexing", index: "Syncing", branch: "Switching branch", publish: "Publishing", hygiene: "Running code health", sonar: "Reading SonarQube", modernize: "Scanning", selfcheck: "Measuring accuracy" };
const DID: Record<string, string> = { add: "Added", index: "Sync", branch: "Branch switch", publish: "Publish", hygiene: "Code health", sonar: "SonarQube", modernize: "Modernize scan", selfcheck: "Accuracy check" };
const elapsed = (s: number) => s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;

/** Running jobs by repo, from the cheap /api/jobs list. The repo list itself is only fetched again when a job ends
 *  (`onSettled`), so rows update in place while work is going on instead of the whole list being reloaded on a timer. */
function useJobs(onSettled: () => void) {
  const [jobs, setJobs] = useState<Record<string, any>>({});
  const seen = useRef<Set<string>>(new Set());
  const timer = useRef<number>();
  const settled = useRef(onSettled);
  settled.current = onSettled;
  const poll = useCallback(async () => {
    window.clearTimeout(timer.current);
    let next = 8000;
    try {
      const list: any[] = document.hidden ? [] : await api.get("/api/jobs");
      if (!document.hidden) {
        const by: Record<string, any> = {};
        for (const j of list) if (j.repo && (!by[j.repo] || (by[j.repo].queued && !j.queued))) by[j.repo] = j;
        const ids = new Set(list.map((j) => j.id as string));
        const ended = [...seen.current].some((id) => !ids.has(id));
        seen.current = ids;
        setJobs((old) => JSON.stringify(old) === JSON.stringify(by) ? old : by);
        if (ended) settled.current();
        if (list.length) next = 1200;
      }
    } catch { /* the server is restarting or the network dropped: keep what is shown and try again */ }
    timer.current = window.setTimeout(poll, next);
  }, []);
  useEffect(() => {
    poll();
    const wake = () => { if (!document.hidden) poll(); };
    document.addEventListener("visibilitychange", wake);
    return () => { window.clearTimeout(timer.current); document.removeEventListener("visibilitychange", wake); };
  }, [poll]);
  return { jobs, poll };
}

/** What ran, when and how it ended: finished jobs the server keeps across restarts. Loaded when opened, and again
 *  whenever `tick` changes (a job ended). */
function Activity({ tick }: { tick: number }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => { if (open) api.get("/api/jobs/history?limit=25").then(setRows).catch(() => setRows([])); }, [open, tick]);
  const when = (iso: string) => { const d = new Date(iso); return isNaN(+d) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); };
  return (
    <details className="card activity" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary><Icon name="clock" />Recent activity<span className="muted">what ran, and how it ended</span></summary>
      {open && (rows === null ? <Empty>Loading…</Empty> : !rows.length ? <Empty>Nothing has run yet. Syncs, publishes and code health runs are listed here.</Empty> : (
        <table>
          <thead><tr><th>When</th><th>Repo</th><th>What</th><th>Result</th><th className="num">Took</th></tr></thead>
          <tbody>
            {rows.map((h) => (
              <tr key={h.id}>
                <td className="muted nowrap">{when(h.at)}</td>
                <td><b>{h.repo || "-"}</b></td>
                <td className="nowrap">{DID[h.kind] || h.kind}</td>
                <td>{h.state === "failed" ? <span className="fail-text"><Badge kind="bad">failed</Badge> {h.error}</span> : <><Badge kind="good">done</Badge> <span className="muted">{h.summary}</span></>}</td>
                <td className="num nowrap">{elapsed(h.seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </details>
  );
}

function BranchPicker({ repo, disabled, onSwitch }: { repo: any; disabled: boolean; onSwitch: (b: string) => void }) {
  const [branches, setBranches] = useState<string[]>([]);
  const [dirty, setDirty] = useState<string[]>([]);
  const load = () => api.get(`/api/repos/${repo.name}/branches`).then((r: any) => { setBranches(r.branches); setDirty(r.dirty); }).catch(() => {});
  useEffect(() => { if (repo.indexed) load(); }, [repo.name, repo.branch]); // eslint-disable-line
  if (!repo.indexed) return null;
  if (!repo.branch && !branches.length) return <span className="rr-branch muted" title="This folder is not a git repo, so it has no branches"><Icon name="folder" />no git</span>;
  const list = repo.branch && !branches.includes(repo.branch) ? [repo.branch, ...branches] : branches;
  return (
    <label className="rr-branch" title={dirty.length ? `Uncommitted changes (${dirty.slice(0, 5).join(", ")}): commit or stash to switch branch` : "Checks out the branch in the clone and re-indexes it"}>
      <Icon name="git" />
      <select value={repo.branch || ""} disabled={disabled || dirty.length > 0} onFocus={load} aria-label={`Branch of ${repo.name}`}
        onChange={(e) => e.target.value && e.target.value !== repo.branch && onSwitch(e.target.value)}>
        {!repo.branch && <option value="">detached HEAD</option>}
        {list.map((b) => <option key={b} value={b}>{b}</option>)}
      </select>
      {dirty.length > 0 && <Badge kind="warn">uncommitted</Badge>}
    </label>
  );
}

/** What needs doing on a repo, most urgent first; empty when it is indexed, current and published. */
function attention(r: any): string[] {
  const out: string[] = [];
  if (r.missing) out.push("folder not found");
  if (r.error) out.push("cannot be read");
  if (r.last_error) out.push("last run failed");
  if (!r.missing && !r.indexed) out.push("not indexed");
  if (r.indexed && r.stale) out.push("behind HEAD");
  if (r.indexed && r.knowledge_problems?.length) out.push(r.knowledge_problems[0] === "not published" ? "not published" : "knowledge out of date");
  if (r.indexed && r.accuracy === "fast") out.push("reduced accuracy");
  return out;
}

type Src = "upload" | "git" | "path";

export default function Repos(_: any) {
  const { data, error, loading, reload } = useRepos();
  const [tick, setTick] = useState(0);
  const { jobs, poll } = useJobs(() => { reload(); setTick((n) => n + 1); });
  const cfg = useConfig().data;
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [src, setSrc] = useState<Src>("upload");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [name, setName] = useState("");
  const [commit, setCommit] = useState(false);
  const [adding, setAdding] = useState(false);
  const [find, setFind] = useState("");
  const [only, setOnly] = useState(false);
  const [syncing, setSyncing] = useState<string[] | null>(null);
  const pathsOk = cfg ? cfg.paths_allowed : true;
  const repos = data || [];

  // the sidebar count and the repo switcher in the top bar follow what this page adds and removes
  const names = repos.map((r) => r.name + (r.indexed ? "+" : "-")).join(",");
  const first = useRef(true);
  useEffect(() => { if (!data) return; if (first.current) { first.current = false; return; } window.dispatchEvent(new Event("devmem-repos")); }, [names]); // eslint-disable-line

  async function act(key: string, fn: () => Promise<any>, ok: (r: any) => string) {
    setBusy((b) => new Set(b).add(key)); setErr(""); setMsg("");
    const started = fn();
    setTimeout(poll, 250);                       // the row shows the job as soon as the server has it
    try { setMsg(ok(await started)); } catch (e: any) { setErr(e.message); }
    setBusy((b) => { const n = new Set(b); n.delete(key); return n; });
    reload(); setTick((n) => n + 1);
  }

  async function syncAll(republish: boolean) {
    setErr(""); setMsg("");
    try {
      const r = await api.post("/api/repos/sync", { republish });
      if (!r.started.length) { setMsg(r.skipped.length ? `Nothing to sync: ${r.skipped.map((s: any) => `${s.repo} (${s.why})`).join(", ")}` : "No repos to sync."); return; }
      setSyncing(r.started.map((s: any) => s.job));
      if (r.skipped.length) setMsg(`Skipped ${r.skipped.map((s: any) => `${s.repo} (${s.why})`).join(", ")}`);
      poll();
    } catch (e: any) { setErr(e.message); }
  }
  // "Sync all" ends when none of its jobs is running any more: say how it went
  const running = Object.keys(jobs).length;
  useEffect(() => {
    if (!syncing || running) return;
    let live = true;
    api.get("/api/jobs?active=false").then((all: any[]) => {
      if (!live) return;
      const mine = all.filter((j) => syncing.includes(j.id));
      if (mine.some((j) => j.state === "running")) return;
      const failed = mine.filter((j) => j.state === "failed");
      setSyncing(null);
      if (failed.length) setErr(`${failed.length} of ${mine.length} repos could not be synced: ${failed.map((j) => `${j.repo} (${j.error})`).join("; ")}`);
      setMsg(`Synced ${mine.length - failed.length} repo${mine.length - failed.length === 1 ? "" : "s"}: ${mine.filter((j) => j.state === "done").reduce((n, j) => n + (j.result?.stats?.parsed || 0), 0)} changed files read again.`);
      reload();
    }).catch(() => setSyncing(null));
    return () => { live = false; };
  }, [syncing, running]); // eslint-disable-line

  const stats = useMemo(() => {
    const indexed = repos.filter((r) => r.indexed);
    return {
      symbols: indexed.reduce((n, r) => n + (r.symbols || 0), 0), files: indexed.reduce((n, r) => n + (r.files || 0), 0),
      fresh: indexed.filter((r) => !r.stale).length, published: indexed.filter((r) => r.knowledge_problems?.length === 0).length,
      attention: repos.filter((r) => attention(r).length).length,
    };
  }, [repos]);
  const shown = repos.filter((r) => (!find.trim() || `${r.name} ${r.path} ${r.branch || ""}`.toLowerCase().includes(find.trim().toLowerCase())) && (!only || attention(r).length > 0));
  const showAdd = adding || (!loading && !repos.length);

  const tab = (id: Src, icon: string, label: string) => (
    <button role="tab" aria-selected={src === id} className={src === id ? "on" : ""} onClick={() => { setSrc(id); setErr(""); }}><Icon name={icon} />{label}</button>
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Repos</h1>
          <p className="sub">Add a repo to index its code. Publish writes the map agents read (<code>.devmem/</code> and an <code>AGENTS.md</code> block) into the repo; nothing else is changed.</p>
        </div>
        <div className="row head-actions">
          {repos.length > 0 && (
            <span className="split">
              <button disabled={!!syncing} onClick={() => syncAll(false)} title="Re-index every repo: only files that changed are read again">
                <span className={syncing ? "spin" : ""}><Icon name="refresh" /></span>{syncing ? `Syncing ${running || ""}…` : "Sync all"}
              </button>
              <Menu disabled={!!syncing} label="More ways to sync" items={[
                { label: "Sync all", icon: "refresh", hint: "re-index every repo; unchanged files are skipped", onClick: () => syncAll(false) },
                { label: "Sync and republish", icon: "send", hint: "also rewrites the knowledge of repos already published", onClick: () => syncAll(true) },
              ]} />
            </span>
          )}
          {repos.length > 0 && <button className="primary" aria-expanded={showAdd} onClick={() => setAdding((a) => !a)}><Icon name="plus" />Add repo</button>}
        </div>
      </div>

      {repos.length > 0 && (
        <div className="kpis">
          <div><span>Repos</span><b>{repos.length}</b><small>{stats.files.toLocaleString()} files</small></div>
          <div><span>Symbols</span><b>{stats.symbols.toLocaleString()}</b><small>functions, classes, methods</small></div>
          <div><span><Term k="stale">Up to date</Term></span><b>{stats.fresh}<i> / {repos.length}</i></b><small>{stats.fresh === repos.length ? "every index matches HEAD" : "Sync all brings the rest up to date"}</small></div>
          <div><span><Term k="publish">Published</Term></span><b>{stats.published}<i> / {repos.length}</i></b><small>{stats.published === repos.length ? "agents read current knowledge" : "agents see the others as unpublished or old"}</small></div>
        </div>
      )}

      {showAdd && (
        <div className="card">
          <div className="row" style={{ marginBottom: 14 }}>
            <h3 style={{ margin: 0 }}>Add a repo</h3><span className="grow" />
            {repos.length > 0 && <button className="ghost sm" onClick={() => setAdding(false)}>Close</button>}
          </div>
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
                <button className="primary" disabled={!url || busy.has("add")} onClick={() => act("add", () => job("/api/repos", { url, branch: branch || undefined, name: name || undefined }), (r) => { setUrl(""); setBranch(""); setName(""); return `Onboarded ${r.name}: ${r.symbols} symbols`; })}>
                  <Icon name="git" />{busy.has("add") ? "Cloning and indexing…" : "Clone and index"}
                </button>
              </div>
              <p className="hint" style={{ margin: "10px 0 0" }}>Cloned into <code>~/.devmem/clones/&lt;name&gt;</code> on {cfg?.deployed ? "the server, with the server's" : "this machine, with its"} git credentials (SSH keys or a credential helper); nothing is prompted. Pick another branch later from the list.</p>
            </>
          )}
          {src === "path" && pathsOk && (
            <>
              <div className="row">
                <input className="grow" placeholder={cfg?.deployed ? "Path on the server, e.g. /srv/repos/orders-etl" : "Path to a local clone, e.g. C:\\work\\orders-etl"} value={path} onChange={(e) => setPath(e.target.value)} aria-label="Repo path" />
                <input placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Repo name" style={{ width: 160 }} />
                <button className="primary" disabled={!path || busy.has("add")} onClick={() => act("add", () => job("/api/repos", { path, name: name || undefined }), (r) => { setPath(""); setName(""); return `Onboarded ${r.name}: ${r.symbols} symbols`; })}>
                  <Icon name="plus" />{busy.has("add") ? "Indexing…" : "Add and index"}
                </button>
              </div>
              <p className="hint" style={{ margin: "10px 0 0" }}>Indexed in place, so it follows your commits and branch switches. The folder must be on the machine that runs devmem.</p>
            </>
          )}
        </div>
      )}

      <Err msg={error || err} />
      {msg && <div className="ok" role="status">{msg}</div>}

      {loading && !data ? <div className="card"><Empty>Loading…</Empty></div> : repos.length > 0 && (
        <div className="card repo-card">
          <div className="repo-tools">
            <div className="search"><Icon name="search" /><input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Filter by name, path or branch" aria-label="Filter repos" /></div>
            <div className="seg" role="tablist" aria-label="Which repos to show">
              <button role="tab" aria-selected={!only} className={!only ? "on" : ""} onClick={() => setOnly(false)}>All <span className="count">{repos.length}</span></button>
              <button role="tab" aria-selected={only} className={only ? "on" : ""} onClick={() => setOnly(true)}>Needs attention <span className="count">{stats.attention}</span></button>
            </div>
            <span className="grow" />
            <span className="muted live" aria-live="polite">{running > 0 ? `${running} ${running === 1 ? "repo" : "repos"} working` : ""}</span>
          </div>
          {!shown.length ? <Empty>{only && !find.trim() ? "Nothing needs attention: every repo is indexed, current and published." : "No repo matches that filter."}</Empty> : (
            <ul className="repo-list">
              {shown.map((r) => {
                const live = jobs[r.name] || (r.working && { ...r.working, id: r.working.job });
                const mine = [...busy].some((k) => k.slice(1) === r.name);
                const locked = !!live || mine;
                const langs = r.langs ? Object.entries(r.langs as Record<string, number>).sort((a, b) => b[1] - a[1]) : [];
                const pct = live && live.total > 0 ? Math.min(100, Math.round((live.done / live.total) * 100)) : null;
                return (
                  <li key={r.name} className={`repo-row${live && !live.queued ? " working" : ""}`}>
                    <div className="rr-main">
                      <div className="rr-id">
                        <span className="rr-ico" title={r.uploaded ? "Uploaded copy" : "Folder on disk"}><Icon name={r.uploaded ? "upload" : "folder"} /></span>
                        <div className="rr-name">
                          <a href={`#/overview?repo=${encodeURIComponent(r.name)}`}>{r.name}</a>
                          <div className="path" title={r.uploaded ? "devmem keeps its own copy of the files you uploaded" : r.path}>{r.uploaded ? "uploaded copy" : r.path}{r.head && <span className="sha" title={`HEAD ${r.head}`}>{r.head.slice(0, 7)}</span>}</div>
                        </div>
                      </div>
                      <div className="rr-status tags">
                        {r.missing ? <Badge kind="bad">folder not found</Badge> : r.error ? <Badge kind="bad"><span title={r.error}>cannot be read</span></Badge>
                          : !r.indexed ? <Badge kind="bad">not indexed</Badge> : r.stale ? <Badge kind="warn"><Term k="stale">behind HEAD</Term></Badge> : <Badge kind="good">up to date</Badge>}
                        {r.indexed && (r.knowledge_problems.length === 0 ? <Badge kind="good">published</Badge> :
                          r.knowledge_problems[0] === "not published" ? <Badge>not published</Badge> :
                          <Badge kind="warn"><span title={r.knowledge_problems.join("\n")}>{r.knowledge_problems.length} knowledge issue{r.knowledge_problems.length === 1 ? "" : "s"}</span></Badge>)}
                        {r.indexed && r.accuracy === "fast" && <Badge kind="bad"><span title="Indexed without the type-aware tools (SCIP, Graphify): call links are less reliable. Sync it without --fast to fix.">reduced accuracy</span></Badge>}
                      </div>
                      {!r.uploaded && <BranchPicker repo={r} disabled={locked} onSwitch={(b) => confirm(`Check out ${b} in ${r.path} and re-index it?`) ? act("b" + r.name, () => job(`/api/repos/${r.name}/branch`, { branch: b }), (x) => `${r.name}: now on ${x.branch}, ${x.symbols} symbols (parsed ${x.stats.parsed}, reused ${x.stats.reused})`) : reload()} />}
                      <div className="row actions">
                        <button className="sm" disabled={locked || !r.indexed} title="Write .devmem/ and the AGENTS.md block that agents read" onClick={() => act("p" + r.name, () => job(`/api/repos/${r.name}/publish`, { commit: commit && !r.uploaded }), (x) => `${r.name}: wrote ${x.written.length} files${x.branch ? ` on branch ${x.branch}` : ""}${r.uploaded ? ". Download the knowledge from the row menu and unpack it in your repo." : ""}`)}><Icon name="send" />Publish</button>
                        <button className="icon-btn bordered" disabled={locked || r.missing} aria-label={`Sync ${r.name}`} title="Sync: read the files that changed again" onClick={() => act("i" + r.name, () => job(`/api/repos/${r.name}/index`), (x) => `${r.name}: parsed ${x.stats.parsed}, reused ${x.stats.reused}`)}><span className={live && !live.queued ? "spin" : ""}><Icon name="refresh" /></span></button>
                        <Menu disabled={locked} label={`More actions for ${r.name}`} items={[
                          { label: "Run code health", icon: "shield", hint: "lint, dead code, complexity", disabled: !r.indexed, onClick: () => act("h" + r.name, () => job(`/api/repos/${r.name}/hygiene`), (x) => `${r.name} code health: ${Object.entries(x.counts).map(([k, v]) => `${k} ${v}`).join(", ") || "nothing found"}`) },
                          ...(r.indexed ? [{ label: "AI analysis", icon: "spark", hint: "descriptions, module summaries, overview", href: `#/analysis?repo=${r.name}` }] : []),
                          ...(r.indexed && r.knowledge_problems[0] !== "not published" ? [{ label: "Download knowledge (.zip)", icon: "download", hint: "unpack at the root of your repo", href: `/api/repos/${r.name}/knowledge.zip` }] : []),
                          { label: "Remove", icon: "trash", danger: true, hint: r.uploaded ? "deletes the uploaded copy" : "your files stay untouched", onClick: () => { if (confirm(r.uploaded ? `Remove ${r.name} and delete its uploaded copy?` : `Remove ${r.name} from devmem? (files stay untouched)`)) act("d" + r.name, () => api.del(`/api/repos/${r.name}`), () => `Removed ${r.name}`); } },
                        ]} />
                      </div>
                    </div>
                    {r.indexed && (
                      <div className="rr-meta">
                        <span><b>{r.symbols?.toLocaleString()}</b> symbols</span>
                        <span><b>{r.files?.toLocaleString()}</b> files</span>
                        {langs.length > 0 && <span title={langs.map(([l, n]) => `${l} ${n}`).join(", ")}>{langs.slice(0, 3).map(([l, n]) => `${l} ${n}`).join(" · ")}{langs.length > 3 ? ` · +${langs.length - 3}` : ""}</span>}
                        {r.hygiene ? <a href={`#/hygiene?repo=${encodeURIComponent(r.name)}`} title={Object.entries(r.hygiene).map(([k, v]) => `${k} ${v}`).join(", ")}>{Object.values(r.hygiene as Record<string, number>).reduce((a, b) => a + (Number(b) || 0), 0).toLocaleString()} code health findings</a> : <span className="muted">code health not run</span>}
                        {r.generated && <span>indexed {r.generated}</span>}
                      </div>
                    )}
                    {live ? (
                      <div className="rr-live" role="status">
                        <div className="rr-bar" role="progressbar" aria-label={`${KIND[live.kind] || "Working"} ${r.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
                          <i className={live.queued ? "" : pct === null ? "busy" : ""} style={live.queued ? { width: 0 } : pct === null ? undefined : { width: `${pct}%` }} />
                        </div>
                        <span>{live.queued ? "Queued" : KIND[live.kind] || "Working"}{live.stage && !live.queued ? ` · ${live.stage}` : ""}{pct !== null ? ` ${live.done.toLocaleString()} / ${live.total.toLocaleString()}` : ""}</span>
                        <span className="muted">{live.queued ? "waiting for a free slot" : elapsed(live.seconds || 0)}</span>
                      </div>
                    ) : r.last_error ? (
                      <div className="rr-fail" role="alert"><b>{KIND[r.last_error.kind] || "The last run"} failed.</b> {r.last_error.error}</div>
                    ) : r.error ? <div className="rr-fail" role="alert">{r.error}</div>
                      : r.missing ? <div className="rr-fail" role="alert">The folder is no longer at this path. Move it back, or remove the repo and add it again from its new place.</div> : null}
                  </li>
                );
              })}
            </ul>
          )}
          {repos.some((r) => !r.uploaded) && <label className="row repo-foot"><input type="checkbox" checked={commit} onChange={(e) => setCommit(e.target.checked)} /> Publish also commits on branch <code>devmem/knowledge-sync</code> (you push and open the PR)</label>}
        </div>
      )}
      {repos.length > 0 && <Activity tick={tick} />}
    </>
  );
}
