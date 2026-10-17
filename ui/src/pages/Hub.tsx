import { useState } from "react";
import { api } from "../api";
import { Badge, Empty, Err, useAsync } from "../components";

function Copy({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  return <button onClick={() => navigator.clipboard?.writeText(text).then(() => { setOk(true); setTimeout(() => setOk(false), 1400); })}>{ok ? "Copied" : "Copy"}</button>;
}

export default function Hub(_: any) {
  const st = useAsync(() => api.get("/api/hub"), []);
  const [spec, setSpec] = useState("");
  const [push, setPush] = useState(false);
  const [summarize, setSummarize] = useState(false);
  const [prune, setPrune] = useState(false);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const h = st.data;

  async function run(key: string, fn: () => Promise<any>, ok: (r: any) => string) {
    setBusy(key); setErr(""); setMsg("");
    try { setMsg(ok(await fn())); st.reload(); } catch (e: any) { setErr(e.message); }
    setBusy("");
  }

  const snippet = h?.spec && h.agent_visible ? h.spec : "https://github.com/<org>/<hub-repo>.git";
  return (
    <>
      <h2>Knowledge hub</h2>
      <p className="sub">One git repo that holds what every onboarded repo knows: searchable code catalogs, overviews, maps and shared lessons. Agents read the hub instead of each repo carrying copies of the others.</p>
      <Err msg={st.error || err} />
      {msg && <div className="ok">{msg}</div>}

      <div className="card">
        <h3>1 · Connect the hub repo</h3>
        <div className="row">
          <input className="grow" placeholder="git URL of an (empty) repo, e.g. git@github.com:acme/devmem-hub.git, or a local folder" value={spec} onChange={(e) => setSpec(e.target.value)} aria-label="Hub git URL or folder" />
          <button className="primary" disabled={!spec.trim() || busy === "set"} onClick={() => run("set", () => api.post("/api/hub", { spec }), (r) => `Hub connected at ${r.dir}`)}>{busy === "set" ? "Connecting…" : h?.configured ? "Change hub" : "Connect"}</button>
        </div>
        {h?.configured && (
          <div className="row" style={{ marginTop: 12 }}>
            <Badge kind="good">connected</Badge><code>{h.spec}</code>
            {!h.agent_visible && <Badge kind="warn">local folder: agents cannot reach it, use a git URL</Badge>}
            {h.agent_visible && !h.remote && <Badge kind="warn">no remote</Badge>}
            {h.dirty && <Badge kind="warn">uncommitted changes</Badge>}
          </div>
        )}
      </div>

      {h?.configured && (
        <div className="card">
          <h3>2 · Sync every onboarded repo into it</h3>
          <div className="row" style={{ gap: 18 }}>
            <label><input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} />Push to the remote after committing</label>
            <label title={h.llm?.configured ? "" : "Set DEVMEM_VERTEX_PROJECT (and install google-genai) to enable"}><input type="checkbox" disabled={!h.llm?.configured} checked={summarize} onChange={(e) => setSummarize(e.target.checked)} />Describe functions with AI <span className="muted">(sends code snippets to Vertex)</span></label>
            <label><input type="checkbox" checked={prune} onChange={(e) => setPrune(e.target.checked)} />Remove repos no longer onboarded</label>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" disabled={!!busy} onClick={() => run("push", () => api.post("/api/hub/push", { push, summarize, prune }),
              (r) => `${r.repos.length} repo(s) synced${r.committed ? ", committed" : ""}${r.pushed ? ", pushed" : ""}${r.note ? ` (${r.note})` : ""}${r.warnings.length ? `. Notes: ${r.warnings.join("; ")}` : ""}`)}>
              {busy === "push" ? "Syncing…" : "Sync all repos to hub"}
            </button>
            {h.last_commit && <span className="muted">last commit: {h.last_commit}</span>}
          </div>
        </div>
      )}

      {h?.configured && (
        <div className="card">
          <h3>In the hub now · {h.repos.length} repos · {h.symbols} reusable symbols</h3>
          {!h.repos.length ? <Empty>Nothing synced yet.</Empty> : (
            <table>
              <thead><tr><th>Repo</th><th>Languages</th><th className="num">Symbols</th><th>Branch</th><th>Origin</th></tr></thead>
              <tbody>{h.repos.map((r: any) => (
                <tr key={r.name}><td><b>{r.name}</b></td><td>{Object.keys(r.langs || {}).join(", ")}</td><td className="num">{r.symbols}</td><td>{r.branch || "-"}</td><td className="mono muted">{r.origin || "-"}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>
      )}

      <div className="card">
        <h3>3 · Point Devin (or any agent) at it</h3>
        <ol style={{ margin: 0, paddingLeft: 20, lineHeight: 1.8 }}>
          <li>Give the agent read access to the hub repo (in Devin: add it to the repositories Devin can use, or make sure its GitHub integration covers it).</li>
          <li>Either run <b>Publish</b> on each repo and merge the PR, which commits <code>.devmem/hub.txt</code> containing the hub URL, or set an environment variable for every session:
            <div className="row" style={{ margin: "6px 0" }}><code>DEVMEM_HUB={snippet}</code><Copy text={`DEVMEM_HUB=${snippet}`} /></div>
          </li>
          <li>The devmem skill then runs <code>devmem hub sync</code> and <code>devmem reuse "&lt;need&gt;"</code> before writing new code. If the hub is unreachable it falls back to the catalog committed in the repo.</li>
        </ol>
        <p className="muted" style={{ marginBottom: 0 }}>This is an instruction to the agent, not an enforced rule: Devin follows the skill and <code>AGENTS.md</code>, and can only read repos it has been given access to.</p>
      </div>
    </>
  );
}
