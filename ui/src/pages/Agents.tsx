import { useEffect, useState } from "react";
import { api, q } from "../api";
import { Badge, Copy, Empty, Err, useRepo, SymbolSearch, Term, useAsync, useRepos } from "../components";

const AGENTS: Record<string, [string, string]> = {
  claude: ["Claude Code", ".mcp.json + CLAUDE.md"], cursor: ["Cursor", ".cursor/mcp.json"], vscode: ["VS Code / Copilot", ".vscode/mcp.json"],
  codex: ["Codex", ".codex/config.toml"], gemini: ["Gemini CLI", ".gemini/settings.json"], kiro: ["Kiro", ".kiro/settings/mcp.json"],
  opencode: ["OpenCode", "opencode.json"], aider: ["Aider", ".aider.conf.yml (CLI, no MCP)"], windsurf: ["Windsurf", "user settings: snippet"],
};
const INSTALL = 'pip install "devmem @ git+https://github.com/rushikeshjaisur11/devmem.git#subdirectory=packages/devmem"';
const MCP_JSON = '{"mcpServers": {"devmem": {"command": "devmem", "args": ["mcp"]}}}';
const n = (x: number) => (x ?? 0).toLocaleString();

function Connect({ repo, onDone }: { repo: string; onDone: () => void }) {
  const st = useAsync(() => (repo ? api.get(`/api/repos/${repo}/connect`) : Promise.resolve(null)), [repo]);
  const [pick, setPick] = useState<string[]>([]);
  const [res, setRes] = useState<any[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setRes(null); setPick([]); }, [repo]);
  const connected: Record<string, boolean> = st.data?.connected || {};
  const go = async () => {
    setBusy(true); setErr("");
    try { setRes((await api.post(`/api/repos/${repo}/connect`, { agents: pick })).results); st.reload(); onDone(); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <div className="card">
      <h3>Connect your agents</h3>
      <p className="hint" style={{ marginTop: -6 }}>Adds devmem as an <Term k="mcp">MCP server</Term> in each agent's project settings, so the agent can call <code>devmem_context</code>, <code>devmem_find</code>, <code>devmem_impact</code> and the rest as tools. Existing settings in those files are kept. Commit them so your team gets the same setup.</p>
      <div className="agents-grid" role="group" aria-label="Agents">
        {Object.entries(AGENTS).map(([k, [label, file]]) => (
          <label key={k} className={`agent-opt ${pick.includes(k) ? "on" : ""}`}>
            <input type="checkbox" checked={pick.includes(k)} onChange={() => setPick(pick.includes(k) ? pick.filter((x) => x !== k) : [...pick, k])} />
            <span>{label}<small>{file}</small></span>
            {connected[k] && <Badge kind="good">connected</Badge>}
          </label>
        ))}
      </div>
      <div className="row">
        <button className="primary" disabled={!repo || !pick.length || busy} onClick={go}>{busy ? "Connecting…" : `Connect ${pick.length || ""} agent${pick.length === 1 ? "" : "s"}`}</button>
        <button className="quiet" onClick={() => setPick(Object.keys(AGENTS))}>Select all</button>
      </div>
      <Err msg={err || st.error} />
      {res && <table style={{ marginTop: 12 }}><tbody>{res.map((r, i) => (
        <tr key={i}><td style={{ width: 120 }}><b>{AGENTS[r.agent]?.[0] || r.agent}</b></td><td className="mono">{r.file}</td>
          <td>{r.note.startsWith("user-level") ? <><Badge kind="warn">paste manually</Badge><pre className="out">{r.note.split("\n").slice(1).join("\n")}</pre></> : <><Badge kind={r.changed ? "good" : ""}>{r.changed ? "added" : "already set"}</Badge> <span className="muted">{r.note}</span></>}</td></tr>
      ))}</tbody></table>}
    </div>
  );
}

function Standalone() {
  return (
    <div className="card">
      <h3>Other ways to use devmem</h3>
      <table><tbody>
        <tr><td style={{ width: 190 }}><b>Any agent, CLI only</b></td><td>Install the CLI, then the agent follows the <code>AGENTS.md</code> block: <div className="row" style={{ marginTop: 6 }}><code>{INSTALL}</code><Copy text={INSTALL} /></div></td></tr>
        <tr><td><b>Any MCP client</b></td><td>Add this to the client's MCP settings: <div className="row" style={{ marginTop: 6 }}><code>{MCP_JSON}</code><Copy text={MCP_JSON} /></div></td></tr>
        <tr><td><b>Claude Code plugin</b></td><td>Installs the skills and starts the MCP server itself (needs Node.js and any Python 3.10+; set <code>DEVMEM_PYTHON</code> if Python is not on PATH): <div className="row" style={{ marginTop: 6 }}><code>/plugin marketplace add rushikeshjaisur11/devmem</code> then <code>/plugin install devmem@devmem</code><Copy text={"/plugin marketplace add rushikeshjaisur11/devmem\n/plugin install devmem@devmem"} /></div></td></tr>
        <tr><td><b>Gemini CLI extension</b></td><td>From a clone of devmem: <div className="row" style={{ marginTop: 6 }}><code>gemini extensions install ./plugins/devmem</code><Copy text="gemini extensions install ./plugins/devmem" /></div></td></tr>
        <tr><td><b>Devin</b></td><td>Install the plugin in <code>plugins/devmem</code> (see docs/INSTALL.md); its skills run the CLI.</td></tr>
      </tbody></table>
    </div>
  );
}

function Doctor({ repo }: { repo: string }) {
  const [rows, setRows] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => setRows(null), [repo]);
  const run = async () => { setBusy(true); setErr(""); try { setRows(await api.get(`/api/repos/${repo}/doctor`)); } catch (e: any) { setErr(e.message); } setBusy(false); };
  const bad = (rows || []).filter((r) => r.ok === false).length;
  return (
    <div className="card">
      <div className="row"><h3 style={{ margin: 0 }}>Check setup</h3><span className="grow" /><button disabled={!repo || busy} onClick={run}>{busy ? "Checking…" : "Run checks"}</button></div>
      <p className="hint" style={{ margin: "6px 0 0" }}>Runs <code>devmem doctor</code>: Python, the CLI on PATH, a fresh index, the AGENTS.md block, a real MCP round trip, and every agent config in the repo.</p>
      <Err msg={err} />
      {rows && <table style={{ marginTop: 10 }}><tbody>{rows.map((r, i) => (
        <tr key={i}><td style={{ width: 90 }}><Badge kind={r.ok ? "good" : r.ok === null ? "warn" : "bad"}>{r.ok ? "ok" : r.ok === null ? "note" : "fix"}</Badge></td><td>{r.what}{!r.ok && <div className="muted" style={{ fontSize: 13.5 }}>{r.fix}</div>}</td></tr>
      ))}</tbody></table>}
      {rows && <p className={bad ? "err" : "ok"}>{bad ? `${bad} problem${bad === 1 ? "" : "s"} to fix.` : "Agents can use devmem in this repo."}</p>}
    </div>
  );
}

function Preview({ repo }: { repo: string }) {
  const [cmd, setCmd] = useState("show");
  const [arg, setArg] = useState("");
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState("");
  const gaps = useAsync(() => (repo ? api.get(`/api/repos/${repo}/gaps?limit=3`) : Promise.resolve([])), [repo]);
  const start = useAsync(() => (repo ? api.get(`/api/repos/${repo}/agent-view?${q({ cmd: "find", arg: "x" })}`) : Promise.resolve(null)), [repo]);
  const run = async (c = cmd, a = arg) => { setErr(""); try { setOut(await api.get(`/api/repos/${repo}/agent-view?${q({ cmd: c, arg: a })}`)); } catch (e: any) { setErr(e.message); } };
  const needsArg = cmd !== "gaps" && cmd !== "helpers";
  return (
    <div className="card">
      <h3>What an agent sees</h3>
      <p className="hint" style={{ marginTop: -6 }}>Every <Term k="session">session</Term> starts with this block{start.data?.session_start ? `, about ${Math.round(start.data.session_start.length / 4)} tokens` : ""}. Everything else the agent asks for, one command at a time.</p>
      {start.data?.session_start ? <pre className="out">{start.data.session_start}</pre> : <Empty>Not published yet: publish the repo to write the AGENTS.md block.</Empty>}
      <div className="row" style={{ marginTop: 16 }}>
        <select value={cmd} onChange={(e) => setCmd(e.target.value)} aria-label="Command">
          {["show", "find", "impact", "recall", "outline", "gaps", "helpers"].map((c) => <option key={c}>{c}</option>)}
        </select>
        {needsArg && (cmd === "show" || cmd === "impact"
          ? <SymbolSearch repo={repo} value={arg} onChange={setArg} onPick={(id) => { setArg(id); run(cmd, id); }} placeholder="Describe the code in plain words or type a name" />
          : <input className="grow mono" value={arg} onChange={(e) => setArg(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()} placeholder={cmd === "outline" ? "path/to/file" : cmd === "recall" ? "the task in one sentence" : "words, e.g. retry upload"} aria-label="Argument" />)}
        <button className="primary" disabled={!repo || (needsArg && !arg.trim())} onClick={() => run()}>Run as agent</button>
      </div>
      {(gaps.data || []).length > 0 && <div className="examples"><span className="muted">Try:</span>{gaps.data.map((g: any) => <button key={g.id} className="chip" onClick={() => { setCmd("show"); setArg(g.id); run("show", g.id); }}>show {g.id.split("#")[1]}</button>)}</div>}
      <Err msg={err} />
      {out && <><p className="hint" style={{ margin: "12px 0 0" }}><code>{out.command}</code> returned about <b>{n(out.tokens)} tokens</b>.</p><pre className="out">{out.output || "(no output)"}</pre></>}
    </div>
  );
}

function Activity({ repo }: { repo: string }) {
  const a = useAsync(() => (repo ? api.get(`/api/repos/${repo}/activity`) : Promise.resolve(null)), [repo]);
  const t = a.data?.total;
  return (
    <div className="card">
      <h3>What agents did</h3>
      {!a.data?.sessions?.length ? <Empty>No agent queries logged in {repo || "this repo"} yet. Each <code>devmem</code> command or MCP call made here is recorded in <code>.devmem/usage.jsonl</code> (not committed).</Empty> : <>
        <p className="hint" style={{ marginTop: -6 }}>{n(t.calls)} queries, answered with {n(t.out_tokens)} <Term k="tokens">tokens</Term> instead of about {n(t.file_tokens)} tokens of the files they point to. Flags mark waste: the same query run again, or an answer over 2,000 tokens.</p>
        {a.data.sessions.map((s: any) => (
          <details className="session" key={s.start}>
            <summary><b>{s.start.slice(0, 16).replace("T", " ")}</b><span className="muted">{s.queries.length} queries via {s.via}</span><span className="muted">{n(s.out_tokens)} tokens</span>{s.waste > 0 && <Badge kind="warn">{s.waste} wasteful</Badge>}</summary>
            <ol>{s.queries.map((qq: any, i: number) => <li key={i}><code>devmem {qq.args || qq.cmd}</code> <span className="muted">{n(qq.out_tokens)} tokens</span> {qq.flags.map((f: string) => <Badge key={f} kind="warn">{f}</Badge>)}</li>)}</ol>
          </details>
        ))}
      </>}
    </div>
  );
}

export default function Agents({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const { repo } = useRepo();
  return (
    <>
      <div className="row"><h1>Agents</h1></div>
      <p className="sub">Connect the coding agents you use, see exactly what they read from devmem, and check what they asked for.</p>
      <Connect repo={repo} onDone={repos.reload} />
      <Doctor repo={repo} />
      <Preview repo={repo} />
      <Activity repo={repo} />
      <Standalone />
    </>
  );
}
