import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Empty, Err, Icon, useAsync } from "../components";

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
        const r = await api.post(`/api/repos/${p.repo}/sonar/sync`);
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
      <p className="sub">Bring findings from tools you already run into Code health, where devmem ranks them by how much code depends on the place they are in, and shows them to agents before they edit that file.</p>
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

      <div className="card">
        <h3>What devmem does with it</h3>
        <ul className="plain">
          <li><b>Ranks</b> each issue by its severity and by how many places call the function it sits in, and whether a test reaches it.</li>
          <li><b>Shows agents</b> the issues of a file with <code>devmem hygiene &lt;file&gt;</code> before they change it.</li>
          <li><b>Stores</b> the findings in <code>.devmem/sonar.json</code> in the repo (paths, rules, messages; not the token).</li>
          <li><b>Does not</b> re-run Sonar's analysis: keep your scanner in CI, and sync here after it has run.</li>
        </ul>
      </div>
    </>
  );
}
