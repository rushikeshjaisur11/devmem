import { useEffect, useState } from "react";
import { api, job, upload } from "../api";
import { Badge, Empty, Err, Icon, JobProgress, useAsync } from "../components";

const plural = (n: number, w: string) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;

/** One repo's Checkmarx One project: name or ID (searchable), optional branch, and Sync now with progress. */
function CxRow({ p, connected, onChange }: { p: any; connected: boolean; onChange: (msg: string, err?: boolean) => void }) {
  const [project, setProject] = useState(p.project || "");
  const [branch, setBranch] = useState(p.branch || "");
  const [busy, setBusy] = useState("");
  const [prog, setProg] = useState<any>(null);
  const [found, setFound] = useState<any[]>([]);
  useEffect(() => { setProject(p.project || ""); setBranch(p.branch || ""); }, [p.project, p.branch]);
  const dirty = project.trim() !== (p.project || "") || branch.trim() !== (p.branch || "");
  const look = async (q: string) => { if (!connected || q.length < 2) return; try { setFound(await api.get(`/api/integrations/checkmarx/projects?q=${encodeURIComponent(q)}`)); } catch { /* search is a convenience */ } };
  const save = async (thenSync: boolean) => {
    setBusy(thenSync ? "sync" : "save");
    try {
      if (dirty) await api.put(`/api/repos/${p.repo}/checkmarx`, { project, branch });
      if (thenSync) {
        const r = await job(`/api/repos/${p.repo}/checkmarx/sync`, {}, setProg);
        const by = Object.entries(r.by_scanner as Record<string, number>).map(([k, n]) => `${k.toUpperCase()} ${n}`).join(", ");
        onChange(`${p.repo}: ${plural(r.issues, "open finding")} from scan ${String(r.scan.id).slice(0, 8)} of ${r.project.name}${by ? ` (${by})` : ""}${r.skipped ? `; ${r.skipped} triaged as not exploitable or fixed were left out` : ""}.${r.notes.length ? " Note: " + r.notes.join("; ") : ""}`);
      } else onChange(`${p.repo}: saved.`);
    } catch (e: any) { onChange(`${p.repo}: ${e.message}`, true); }
    setBusy(""); setProg(null);
  };
  return (
    <>
      <tr>
        <td><b>{p.repo}</b></td>
        <td>
          <input list={`cx-${p.repo}`} value={project} placeholder={`project name (default: ${p.guess})`} aria-label={`Checkmarx One project for ${p.repo}`} style={{ width: "100%", minWidth: 220 }} onChange={(e) => { setProject(e.target.value); look(e.target.value); }} />
          <datalist id={`cx-${p.repo}`}>{found.map((f) => <option key={f.id} value={f.name}>{f.id}</option>)}</datalist>
        </td>
        <td><input value={branch} placeholder="any branch" aria-label={`Branch for ${p.repo}`} style={{ width: 130 }} onChange={(e) => setBranch(e.target.value)} /></td>
        <td>{p.synced ? <><Badge kind="good">{plural(p.issues, "finding")}</Badge><div className="muted" style={{ fontSize: 12.5 }}>{p.synced.slice(0, 16).replace("T", " ")}</div></> : <span className="muted">never</span>}</td>
        <td>
          <div className="row actions">
            {dirty && <button className="sm" disabled={!!busy} onClick={() => save(false)}>Save</button>}
            <button className="primary sm" disabled={!!busy || !connected} onClick={() => save(true)}><Icon name="refresh" />{busy === "sync" ? "Reading…" : "Sync now"}</button>
            {p.synced && <a className="btn-link" href={`#/hygiene?repo=${p.repo}`}>Open code health</a>}
          </div>
        </td>
      </tr>
      {prog && <tr><td colSpan={5}><JobProgress job={prog} label="Reading Checkmarx One" /></td></tr>}
    </>
  );
}

function Checkmarx({ repos }: { repos: any[] }) {
  const st = useAsync<any>(() => api.get("/api/integrations/checkmarx"), []);
  const s = st.data;
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const [tenant, setTenant] = useState("");
  const [iam, setIam] = useState("");
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [test, setTest] = useState<any>(null);
  useEffect(() => { if (s) { setUrl(s.url || ""); setTenant(s.tenant || ""); setIam(s.iam_url || ""); if (s.tenant || s.iam_url) setMore(true); } }, [s?.url, s?.tenant, s?.iam_url]); // eslint-disable-line
  if (!s) return <div className="card"><h3>Checkmarx One</h3><Err msg={st.error} />{!st.error && <p className="muted">Loading…</p>}</div>;
  const env = (k: string) => s.from_environment.includes(k);
  const save = async () => {
    setBusy("save"); setErr(""); setTest(null);
    try { await api.put("/api/integrations/checkmarx", { url, api_key: key || undefined, tenant, iam_url: iam }); setKey(""); st.reload(); setTest(await api.post("/api/integrations/checkmarx/test")); } catch (e: any) { setErr(e.message); }
    setBusy("");
  };
  const forget = async () => { setBusy("off"); setErr(""); try { await api.put("/api/integrations/checkmarx", { url: "", tenant: "", iam_url: "", clear_key: true }); setTest(null); st.reload(); } catch (e: any) { setErr(e.message); } setBusy(""); };
  return (
    <>
      <div className="card">
        <div className="row" style={{ marginBottom: 16 }}>
          <h3 style={{ margin: 0 }}>Checkmarx One</h3>
          {s.configured ? <Badge kind="good">connected</Badge> : <Badge>not connected</Badge>}
          <span className="muted" style={{ fontSize: 13 }}>SAST, SCA (open-source dependencies) and KICS (infrastructure as code)</span>
        </div>
        <Err msg={err} />
        <div className="form-grid">
          <label className="field">Checkmarx One address
            <input value={url} disabled={env("url")} onChange={(e) => setUrl(e.target.value)} placeholder="https://ast.checkmarx.net" />
            <span className="hint">Your region's address: <code>https://ast.checkmarx.net</code>, <code>https://eu.ast.checkmarx.net</code>, <code>https://anz.ast.checkmarx.net</code> …, or your single-tenant address.</span>
          </label>
          <label className="field">API key
            <input type="password" autoComplete="off" value={key} disabled={env("api_key")} onChange={(e) => setKey(e.target.value)} placeholder={env("api_key") ? "set by an environment variable" : s.has_key ? `saved ${s.key_hint}  (type to replace)` : "eyJhbGciOi…"} />
            <span className="hint">In Checkmarx One: your user menu → API Keys → Generate. The user needs a role that can view projects and results. Stored on the machine that runs devmem; never shown again and never written into a repo.</span>
          </label>
        </div>
        {!more ? <button className="ghost sm" style={{ marginTop: 10 }} onClick={() => setMore(true)}>Tenant and login address (only if the key is not accepted)</button> : (
          <div className="form-grid" style={{ marginTop: 14 }}>
            <label className="field">Tenant
              <input value={tenant} disabled={env("tenant")} onChange={(e) => setTenant(e.target.value)} placeholder="acme" />
              <span className="hint">Usually not needed: an API key names its own tenant. Older keys do not; then enter the tenant you log in to.</span>
            </label>
            <label className="field">Login (IAM) address
              <input value={iam} disabled={env("iam_url")} onChange={(e) => setIam(e.target.value)} placeholder="https://iam.checkmarx.net" />
              <span className="hint">Leave empty to use the one beside the address above (<code>ast.</code> → <code>iam.</code>).</span>
            </label>
          </div>
        )}
        <div className="row" style={{ marginTop: 18 }}>
          <button className="primary" disabled={!!busy || !url.trim() || (!key && !s.has_key && !env("api_key"))} onClick={save}><Icon name="plug" />{busy === "save" ? "Testing…" : "Save and test"}</button>
          {(s.configured || s.has_key) && !env("url") && <button className="ghost danger" disabled={!!busy} onClick={forget}>Disconnect and forget the key</button>}
        </div>
        {test && (test.ok
          ? <div className="ok">Connected. The key logs in at <code>{test.realm}</code> and sees {plural(test.projects, "project")}.</div>
          : <div className="err" role="alert">{test.error}</div>)}
      </div>
      {s.configured && (
        <div className="card">
          <h3>Which Checkmarx One project is each repo?</h3>
          <p className="hint" style={{ marginTop: -8 }}>Sync reads the latest <b>completed</b> scan of the project (of the branch, when you give one): open SAST, SCA and KICS results. Results triaged as not exploitable are left out. It only reads; scans keep running in your CI.</p>
          {msg && <div className={msg.startsWith("!") ? "err" : "ok"}>{msg.replace(/^!/, "")}</div>}
          {!s.projects.length ? <Empty>No repos yet. <a href="#/repos">Add one</a> first.</Empty> : (
            <table>
              <thead><tr><th>Repo</th><th>Checkmarx One project</th><th>Branch</th><th>Last sync</th><th></th></tr></thead>
              <tbody>{s.projects.map((p: any) => <CxRow key={p.repo} p={p} connected={s.configured} onChange={(m, bad) => { setMsg((bad ? "!" : "") + m); st.reload(); }} />)}</tbody>
            </table>
          )}
        </div>
      )}
      <Sarif repos={repos} />
    </>
  );
}

/** Upload a SARIF report per repo: Checkmarx (cx CLI, CxSAST), CodeQL, Semgrep, Snyk … */
function Sarif({ repos }: { repos: any[] }) {
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");
  const [lists, setLists] = useState<Record<string, any[]>>({});
  const indexed = repos.filter((r) => r.indexed);
  const load = () => Promise.all(indexed.map((r) => api.get(`/api/repos/${r.name}/sarif`).then((l: any[]) => [r.name, l] as const).catch(() => [r.name, []] as const))).then((x) => setLists(Object.fromEntries(x)));
  useEffect(() => { load(); }, [indexed.map((r) => r.name).join()]); // eslint-disable-line
  const send = async (repo: string, f: File) => {
    setBusy(repo); setMsg("");
    try {
      const r = await upload(`/api/repos/${repo}/sarif?filename=${encodeURIComponent(f.name)}`, f, () => {});
      setMsg(`${repo}: ${r.names.join(", ")}: ${plural(r.issues, "finding")} imported, ${r.matched.toLocaleString()} in files of this repo.${r.notes.length ? " Note: " + r.notes.join("; ") : ""}`);
      load();
    } catch (e: any) { setMsg(`!${repo}: ${e.message}`); }
    setBusy("");
  };
  const drop = async (repo: string, tool: string) => { try { await api.del(`/api/repos/${repo}/sarif/${tool}`); load(); } catch (e: any) { setMsg(`!${e.message}`); } };
  return (
    <div className="card">
      <h3>SARIF reports (Checkmarx SAST on-premises, cx CLI, CodeQL, Semgrep …)</h3>
      <p className="hint" style={{ marginTop: -8 }}>
        Any scanner that writes SARIF 2.1.0. From Checkmarx: <code>cx scan create … --report-format sarif</code> (writes <code>cx_result.sarif</code>), or the SARIF report of CxSAST / CxFlow.
        A newer report from the same tool replaces the earlier one. A <code>*.sarif</code> file your CI leaves at the repo root is imported on every re-index, and <code>devmem-app sarif &lt;repo&gt; &lt;file&gt;</code> does it from a pipeline.
      </p>
      {msg && <div className={msg.startsWith("!") ? "err" : "ok"}>{msg.replace(/^!/, "")}</div>}
      {!indexed.length ? <Empty>No indexed repos yet. <a href="#/repos">Add one</a> first.</Empty> : (
        <table>
          <thead><tr><th>Repo</th><th>Imported reports</th><th></th></tr></thead>
          <tbody>{indexed.map((r) => (
            <tr key={r.name}>
              <td><b>{r.name}</b></td>
              <td>{(lists[r.name] || []).length ? <div className="tags">{lists[r.name].map((t) => (
                <span className="tag" key={t.tool} title={`${t.file}, imported ${t.ts.slice(0, 16).replace("T", " ")}`}>{t.name} <b>{t.issues.toLocaleString()}</b>
                  <button className="tag-x" aria-label={`Remove the ${t.name} report from ${r.name}`} onClick={() => drop(r.name, t.tool)}>×</button></span>))}</div> : <span className="muted">none</span>}</td>
              <td><div className="row actions">
                <label className="btn-file"><input type="file" hidden accept=".sarif,.json" disabled={!!busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) send(r.name, f); e.target.value = ""; }} /><span className="btnlike"><Icon name="upload" />{busy === r.name ? "Importing…" : "Import SARIF"}</span></label>
                {(lists[r.name] || []).length > 0 && <a className="btn-link" href={`#/hygiene?repo=${r.name}`}>Open code health</a>}
              </div></td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}

function ProjectRow({ p, connected, onChange }: { p: any; connected: boolean; onChange: (msg: string, err?: boolean) => void }) {
  const [key, setKey] = useState(p.key || p.detected || "");
  const [branch, setBranch] = useState(p.branch || "");
  const [busy, setBusy] = useState("");
  const [found, setFound] = useState<any[]>([]);
  useEffect(() => { setKey(p.key || p.detected || ""); setBranch(p.branch || ""); }, [p.key, p.branch, p.detected]);
  const dirty = key.trim() !== (p.key || "") || branch.trim() !== (p.branch || "");
  const look = async (q: string) => { if (!connected || q.length < 2) return; try { setFound(await api.get(`/api/integrations/sonar/projects?q=${encodeURIComponent(q)}`)); } catch { /* search is a convenience */ } };
  const save = async (thenSync: boolean) => {
    setBusy(thenSync ? "sync" : "save");
    try {
      if (dirty) await api.put(`/api/repos/${p.repo}/sonar`, { key, branch });
      if (thenSync) {
        const r = await job(`/api/repos/${p.repo}/sonar/sync`);
        onChange(`${p.repo}: ${r.issues.toLocaleString()} open issue${r.issues === 1 ? "" : "s"} and ${r.hotspots} security hotspot${r.hotspots === 1 ? "" : "s"} read from SonarQube${r.truncated ? ` (the ${r.issues.toLocaleString()} most severe of ${r.total.toLocaleString()})` : ""}.${r.notes.length ? " Note: " + r.notes.join("; ") : ""}`);
      } else onChange(`${p.repo}: saved.`);
    } catch (e: any) { onChange(`${p.repo}: ${e.message}`, true); }
    setBusy("");
  };
  return (
    <tr>
      <td><b>{p.repo}</b></td>
      <td>
        <input list={`sq-${p.repo}`} value={key} placeholder="project key" aria-label={`SonarQube project key for ${p.repo}`} style={{ width: "100%", minWidth: 200 }} onChange={(e) => { setKey(e.target.value); look(e.target.value); }} />
        <datalist id={`sq-${p.repo}`}>{found.map((f) => <option key={f.key} value={f.key}>{f.name}</option>)}</datalist>
        {p.detected && !p.key && <div className="hint">found in the repo's Sonar config</div>}
      </td>
      <td><input value={branch} placeholder="default" aria-label={`Branch for ${p.repo}`} style={{ width: 130 }} onChange={(e) => setBranch(e.target.value)} /></td>
      <td>{p.synced ? <><Badge kind="good">{p.issues.toLocaleString()} issues</Badge><div className="muted" style={{ fontSize: 12.5 }}>{p.synced.slice(0, 16).replace("T", " ")}</div></> : <span className="muted">never</span>}</td>
      <td>
        <div className="row actions">
          {dirty && <button className="sm" disabled={!!busy} onClick={() => save(false)}>Save</button>}
          <button className="primary sm" disabled={!!busy || !connected || !key.trim()} onClick={() => save(true)}><Icon name="refresh" />{busy === "sync" ? "Reading…" : "Sync now"}</button>
          {p.synced && <a className="btn-link" href={`#/hygiene?repo=${p.repo}`}>Open code health</a>}
        </div>
      </td>
    </tr>
  );
}

export default function Integrations(_: any) {
  const st = useAsync<any>(() => api.get("/api/integrations/sonar"), []);
  const repoList = useAsync<any[]>(() => api.get("/api/repos"), []);
  const s = st.data;
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [test, setTest] = useState<any>(null);
  useEffect(() => { if (s) setUrl(s.url || ""); }, [s?.url]); // eslint-disable-line
  if (!s) return <><h1>Integrations</h1><Err msg={st.error} />{!st.error && <p className="muted">Loading…</p>}</>;
  const env = (k: string) => s.from_environment.includes(k);

  const save = async () => {
    setBusy("save"); setErr(""); setMsg(""); setTest(null);
    try { await api.put("/api/integrations/sonar", { url, token: token || undefined }); setToken(""); st.reload(); setTest(await api.post("/api/integrations/sonar/test")); } catch (e: any) { setErr(e.message); }
    setBusy("");
  };
  const forget = async () => { setBusy("off"); setErr(""); try { await api.put("/api/integrations/sonar", { url: "", clear_token: true }); setTest(null); st.reload(); } catch (e: any) { setErr(e.message); } setBusy(""); };

  return (
    <>
      <h1>Integrations</h1>
      <p className="sub">Bring findings from tools you already run (SonarQube, Checkmarx One, or any scanner that writes SARIF) into Code health, where devmem ranks them by how much code depends on the place they are in, and shows them to agents before they edit that file.</p>
      <Err msg={st.error || err} />

      <div className="card">
        <div className="row" style={{ marginBottom: 16 }}>
          <h3 style={{ margin: 0 }}>SonarQube Server</h3>
          {s.configured ? <Badge kind="good">connected</Badge> : <Badge>not connected</Badge>}
        </div>
        <div className="form-grid">
          <label className="field">Server address
            <input value={url} disabled={env("url")} onChange={(e) => setUrl(e.target.value)} placeholder="https://sonarqube.mycompany.com" />
            <span className="hint">The address you open SonarQube at, without <code>/dashboard</code>. The machine that runs devmem must be able to reach it.</span>
          </label>
          <label className="field">User token
            <input type="password" autoComplete="off" value={token} disabled={env("token")} onChange={(e) => setToken(e.target.value)} placeholder={env("token") ? "set by an environment variable" : s.has_token ? `saved ${s.token_hint}  (type to replace)` : "squ_…"} />
            <span className="hint">In SonarQube: My Account → Security → Generate token (type: User). It needs Browse permission on the projects. Stored on the machine that runs devmem; never shown again and never written into a repo.</span>
          </label>
        </div>
        <div className="row" style={{ marginTop: 18 }}>
          <button className="primary" disabled={!!busy || !url.trim()} onClick={save}><Icon name="plug" />{busy === "save" ? "Testing…" : "Save and test"}</button>
          {(s.configured || s.has_token) && !env("url") && <button className="ghost danger" disabled={!!busy} onClick={forget}>Disconnect and forget the token</button>}
        </div>
        {test && (test.ok
          ? <div className="ok">Connected to SonarQube {test.version}.{test.token_valid === null ? " No token saved: only public projects can be read." : " The token works."}</div>
          : <div className="err" role="alert">{test.error}</div>)}
      </div>

      <div className="card">
        <h3>Which SonarQube project is each repo?</h3>
        <p className="hint" style={{ marginTop: -8 }}>The project key is in the address of the project's page in SonarQube (<code>?id=…</code>). Sync reads open issues, security hotspots, the quality gate, coverage and duplication. It only reads; nothing is changed in SonarQube.</p>
        {msg && <div className={msg.startsWith("!") ? "err" : "ok"}>{msg.replace(/^!/, "")}</div>}
        {!s.projects.length ? <Empty>No repos yet. <a href="#/repos">Add one</a> first.</Empty> : (
          <table>
            <thead><tr><th>Repo</th><th>SonarQube project key</th><th>Branch</th><th>Last sync</th><th></th></tr></thead>
            <tbody>{s.projects.map((p: any) => <ProjectRow key={p.repo} p={p} connected={s.configured} onChange={(m, bad) => { setMsg((bad ? "!" : "") + m); st.reload(); }} />)}</tbody>
          </table>
        )}
      </div>

      <Checkmarx repos={repoList.data || []} />

      <div className="card">
        <h3>What devmem does with it</h3>
        <ul className="plain">
          <li><b>Ranks</b> each finding (SonarQube, Checkmarx, SARIF) by its severity and by how many places call the function it sits in, and whether a test reaches it.</li>
          <li><b>Shows agents</b> the issues of a file with <code>devmem hygiene &lt;file&gt;</code> before they change it.</li>
          <li><b>Stores</b> the findings in <code>.devmem/sonar.json</code>, <code>.devmem/checkmarx.json</code> and <code>.devmem/sarif.json</code> in the repo (paths, rules, messages; never a token or key).</li>
          <li><b>Does not</b> run the scanners: keep them in CI, and sync or import here after they have run.</li>
        </ul>
      </div>
    </>
  );
}
