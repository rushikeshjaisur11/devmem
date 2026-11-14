import { useState } from "react";
import { api, q } from "../api";
import { Badge, Empty, Err, fmtBytes, Icon, Menu, useAsync, useConfig, useRepo } from "../components";
import { UploadBox } from "../UploadBox";

const EXAMPLES: [string, string, string][] = [
  ["Python package", "pypi:pydantic", "latest release; pypi:pydantic==1.10.13 for a fixed one"],
  ["npm package", "npm:zod", "latest release; npm:zod@3.22.4 for a fixed one"],
  ["llms.txt", "https://docs.example.dev/llms.txt", "the best source when the library publishes one"],
  ["Any docs page", "https://docs.example.dev/guide/", "a single page, converted to text"],
];
const KIND: Record<string, [string, string]> = { pypi: ["PyPI", "globe"], npm: ["npm", "globe"], url: ["Web", "globe"], local: ["Folder", "folder"], upload: ["Upload", "upload"] };

export default function Docs(_: any) {
  const libs = useAsync<any[]>(() => api.get("/api/docs"), []);
  const cfg = useConfig().data;
  const { repo } = useRepo();
  const pathsOk = cfg ? cfg.paths_allowed : true;
  const [src, setSrc] = useState<"web" | "upload" | "path">("web");
  const [spec, setSpec] = useState("");
  const [path, setPath] = useState("");
  const [follow, setFollow] = useState(true);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<any[] | null>(null);

  async function run(key: string, fn: () => Promise<any>, ok: (r: any) => string) {
    setBusy(key); setErr(""); setMsg("");
    try { setMsg(ok(await fn())); libs.reload(); } catch (e: any) { setErr(e.message); }
    setBusy("");
  }
  const added = (r: any) => `Added ${r.name}${r.version ? " " + r.version : ""}: ${r.pages} page${r.pages === 1 ? "" : "s"}, ${fmtBytes(r.bytes)}. Publish a repo that uses it to ship the docs to agents.`;
  async function search() {
    setErr("");
    try { setHits(await api.get(`/api/docs/search?${q({ q: query, repo })}`)); } catch (e: any) { setErr(e.message); }
  }
  const list = libs.data || [];
  const old = list.filter((l) => l.age_days > 120).length;
  const tab = (id: typeof src, icon: string, label: string) => <button role="tab" aria-selected={src === id} className={src === id ? "on" : ""} onClick={() => { setSrc(id); setErr(""); }}><Icon name={icon} />{label}</button>;

  return (
    <>
      <h1>Library docs</h1>
      <p className="sub">Agents usually cannot browse the web, so they write against whatever API the old code in the repo uses. Put the current documentation here once; Publish copies it into each repo that uses the library, and agents search it offline with <code>devmem docs</code>.</p>
      <Err msg={libs.error || err} />
      {msg && <div className="ok">{msg}</div>}

      <div className="card">
        <h3>Add documentation</h3>
        <div className="seg big" role="tablist" style={{ marginBottom: 16 }}>
          {tab("web", "globe", "From the web")}
          {tab("upload", "upload", "Upload files")}
          {pathsOk && tab("path", "folder", cfg?.deployed ? "Path on the server" : "Folder on this computer")}
        </div>
        {src === "web" && (
          <>
            <div className="row">
              <input className="grow" placeholder="pypi:pydantic, npm:zod, or https://docs.example.dev/llms.txt" value={spec} onChange={(e) => setSpec(e.target.value)} onKeyDown={(e) => e.key === "Enter" && spec.trim() && busy !== "add" && run("add", () => api.post("/api/docs", { spec, follow }), added)} aria-label="Package or address" />
              <button className="primary" disabled={!spec.trim() || busy === "add"} onClick={() => run("add", () => api.post("/api/docs", { spec, follow }), added)}><Icon name="download" />{busy === "add" ? "Fetching…" : "Fetch docs"}</button>
            </div>
            <label className="row" style={{ marginTop: 10 }} title="For an llms.txt index: also fetch the pages it links to (up to 40)"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />Also fetch the pages an <code>llms.txt</code> links to</label>
            <div className="doc-examples">
              {EXAMPLES.map(([label, ex, note]) => (
                <button key={label} className="example" onClick={() => setSpec(ex)} title="Use this as a starting point">
                  <b>{label}</b><span className="mono">{ex}</span><span>{note}</span>
                </button>
              ))}
            </div>
          </>
        )}
        {src === "upload" && (
          <UploadBox maxMb={cfg?.max_upload_mb || 300} onError={setErr} onDone={(r) => { setMsg(added(r)); libs.reload(); }}
            endpoint={(n, f) => `/api/docs/upload?name=${encodeURIComponent(n)}&filename=${encodeURIComponent(f)}`}
            text={{ title: "Drop a docs folder or a .zip here", note: <>Markdown, text and HTML files are read; internal guides, API references, exported wikis</>, nameLabel: "Library or guide name", action: "Upload and add", working: "Uploaded. Reading the pages…" }} />
        )}
        {src === "path" && pathsOk && (
          <>
            <div className="row">
              <input className="grow" placeholder={cfg?.deployed ? "/srv/docs/internal-api" : "C:\\docs\\internal-api  or  ~/docs/internal-api"} value={path} onChange={(e) => setPath(e.target.value)} aria-label="Folder path" />
              <button className="primary" disabled={!path.trim() || busy === "add"} onClick={() => run("add", () => api.post("/api/docs", { spec: path, follow: false }), added)}><Icon name="plus" />{busy === "add" ? "Reading…" : "Add folder"}</button>
            </div>
            <p className="hint" style={{ margin: "10px 0 0" }}>Read in place from the machine that runs devmem, so <b>Refresh all</b> picks up edits to the folder.</p>
          </>
        )}
      </div>

      <div className="card">
        <div className="toolbar">
          <h3 style={{ margin: 0 }}>On file</h3>
          <span className="muted">{list.length} librar{list.length === 1 ? "y" : "ies"}{old ? `, ${old} older than 4 months` : ""}</span>
          <span className="toolbar-end">
            <button disabled={!!busy || !list.length || !repo} title="Release notes between the version this repo uses and the latest, saved with each library's docs"
              onClick={() => run("notes", () => api.post("/api/docs/upgrade-notes", { repo }), (r) => Object.keys(r).length ? Object.entries(r).map(([k, v]) => `${k}: ${v}`).join("; ") + ". Publish the repo to ship them." : `${repo} is on the latest version of every library with docs here.`)}>
              {busy === "notes" ? "Fetching…" : `Get upgrade notes for ${repo || "a repo"}`}</button>
            <button disabled={!!busy || !list.length} onClick={() => run("refresh", () => api.post("/api/docs/refresh"), (r) => Object.entries(r).map(([k, v]) => `${k}: ${v}`).join(" · "))}><span className={busy === "refresh" ? "spin" : ""}><Icon name="refresh" /></span>{busy === "refresh" ? "Refreshing…" : "Refresh all"}</button>
          </span>
        </div>
        {libs.loading && !list.length ? <Empty>Loading…</Empty> : !list.length ? <Empty>No documentation yet. Fetch a package above, or upload your internal guides.</Empty> : (
          <table>
            <thead><tr><th>Library</th><th>Version</th><th>Fetched</th><th className="num">Pages</th><th className="num">Size</th><th>From</th><th></th></tr></thead>
            <tbody>{list.map((l) => (
              <tr key={l.name}>
                <td><b>{l.name}</b></td>
                <td className="mono">{l.version || <span className="muted">-</span>}</td>
                <td>{l.age_days > 120 ? <Badge kind="warn">{l.age_days} days old</Badge> : <Badge kind="good">{l.age_days <= 1 ? "today" : `${l.age_days} days ago`}</Badge>}</td>
                <td className="num">{l.pages}</td>
                <td className="num">{l.bytes ? fmtBytes(l.bytes) : "-"}</td>
                <td><span className="tag" title={l.source}><Icon name={(KIND[l.kind] || KIND.url)[1]} /> {(KIND[l.kind] || KIND.url)[0]}</span> <span className="mono muted src">{String(l.source).replace(/^(uploaded: |https?:\/\/)/, "")}</span></td>
                <td><div className="row actions"><Menu label={`Actions for ${l.name}`} disabled={!!busy} items={[
                  { label: "Remove", icon: "trash", danger: true, hint: "agents keep the copy already published in repos", onClick: () => { if (confirm(`Remove the ${l.name} docs?`)) run("rm" + l.name, () => api.del(`/api/docs/${l.name}`), () => `Removed ${l.name}.`); } },
                ]} /></div></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>Check what an agent finds</h3>
        <div className="row">
          <input className="grow" placeholder="e.g. validate an email address" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && query.trim() && repo && search()} aria-label="Docs query" />
          <button disabled={!query.trim() || !repo} onClick={search}><Icon name="search" />Search in {repo || "a repo"}</button>
        </div>
        <p className="hint" style={{ margin: "8px 0 0" }}>Searches the docs published inside the repo chosen in the top bar (<code>.devmem/docs</code>), exactly as <code>devmem docs</code> does for an agent.</p>
        {hits && (hits.length === 0 ? <Empty>Nothing in {repo} matches. Publish the repo after adding docs, or add the library.</Empty> :
          hits.map((h, i) => (
            <div key={i} className="doc-hit">
              <div className="row"><b>{h.heading || h.title}</b><Badge>{h.lib} {h.version}</Badge><span className="muted">fetched {h.fetched}</span>{/^https?:/.test(h.url) && <a href={h.url} target="_blank" rel="noreferrer">source</a>}</div>
              <pre className="code">{h.text.slice(0, 900)}</pre>
            </div>
          )))}
      </div>
    </>
  );
}
