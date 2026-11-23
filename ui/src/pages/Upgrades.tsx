import { useEffect, useState } from "react";
import { api, job } from "../api";
import { nav } from "../route";
import { Badge, Copy, EditorLink, Empty, Err, Icon, JobProgress, editorHref, useAsync, useRepo, useRepos } from "../components";

const LEVEL: Record<string, [string, string]> = { major: ["major", "warn"], minor: ["minor", ""], patch: ["patch", "good"], unknown: ["version not pinned", ""] };
const HOURS: [number, string][] = [[6, "every 6 hours"], [12, "every 12 hours"], [24, "every day"], [168, "every week"], [0, "never"]];
const when = (ts: string) => (ts ? ts.slice(0, 16).replace("T", " ").replace("Z", "") : "never");
const Md = ({ text }: { text: string }) => <>{text.split("`").map((t, i) => (i % 2 ? <code key={i}>{t}</code> : <span key={i}>{t}</span>))}</>;

export default function Upgrades(_: any) {
  const repos = useRepos();
  const { repo } = useRepo();
  const rel = useAsync<any>(() => api.get("/api/releases"), []);
  const cands = useAsync<any>(() => (repo ? api.get(`/api/repos/${repo}/upgrades`) : Promise.resolve(null)), [repo]);
  const search = useAsync<any>(() => api.get("/api/settings/search"), []);
  const llm = useAsync<any>(() => api.get("/api/chat/status"), []);
  const [plan, setPlan] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [prog, setProg] = useState<any>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [web, setWeb] = useState(true);
  const [ai, setAi] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  useEffect(() => { setPlan(null); setMsg(""); setErr(""); }, [repo]);

  async function run(key: string, fn: () => Promise<any>, ok?: (r: any) => string) {
    setBusy(key); setErr(""); setMsg("");
    try { const r = await fn(); if (ok) setMsg(ok(r)); } catch (e: any) { setErr(e.message); }
    setBusy(""); setProg(null);
  }
  const check = () => run("check", () => job("/api/releases/check", {}, setProg), (r) => {
    rel.reload(); cands.reload();
    return `Checked ${r.checked} package${r.checked === 1 ? "" : "s"}: ${r.new.length ? `${r.new.length} new release${r.new.length === 1 ? "" : "s"}` : "no new releases"}.`
      + (Object.keys(r.errors || {}).length ? ` ${Object.keys(r.errors).length} could not be read.` : "") + (r.webhook ? ` ${r.webhook}.` : "");
  });
  const build = (lib: string, inRepo = repo, to?: string) => run("plan:" + lib, async () => {
    const r = await job(`/api/repos/${inRepo}/upgrades`, { lib, to, search: web, ai }, setProg);
    setPlan({ ...r, repo: inRepo }); cands.reload();
    return r;
  }, (r) => `Upgrade prompt for ${r.lib} ${r.from || "?"} → ${r.to} is ready (~${r.tokens.toLocaleString()} tokens). Agents can also print it with \`devmem upgrade ${r.lib}\` after the next publish.`);
  const open = (lib: string) => run("open:" + lib, async () => setPlan({ ...(await api.get(`/api/repos/${repo}/upgrades/${lib}`)), repo }));
  const refreshVersions = () => run("versions", async () => { const r = await api.get(`/api/repos/${repo}/upgrades?refresh=true`); cands.reload(); return r; }, (r) => `Asked the registries: ${r.libs.length} of ${repo}'s libraries have a newer release.`);
  const setSetting = (body: any) => api.put("/api/releases/settings", body).then(rel.reload).catch((x) => setErr(x.message));

  const st = rel.data;
  const events: any[] = st?.events || [];
  const libs: any[] = cands.data?.libs || [];
  const unread = events.filter((e) => !e.read);
  const s = st?.settings || {};
  const sp = search.data;

  return (
    <>
      <div className="row">
        <h1>Upgrades</h1>
        <span className="grow" />
        <button className={st?.last_check ? "" : "primary"} disabled={!!busy} onClick={check} title="Ask PyPI and npm for the newest release of every library your repos use. Read from the registries and the projects' git repositories; nothing is changed in any repo.">
          <span className={busy === "check" ? "spin" : ""}><Icon name="refresh" /></span>{busy === "check" ? "Checking…" : "Check for new releases"}</button>
      </div>
      <p className="sub">devmem watches the libraries your repos depend on. When one publishes a release it reads what changed from the project's own git repository and docs, searches the web for migration guides, finds the lines in your repo that the changes touch, and writes a step-by-step upgrade prompt for your coding agent. It never edits your code.</p>
      <Err msg={rel.error || cands.error || err} />
      {msg && <div className="ok"><Md text={msg} /></div>}
      {prog && <div className="card"><JobProgress job={prog} label={busy === "check" ? "Checking the registries" : "Preparing the upgrade prompt"} /></div>}

      {plan && <PlanView plan={plan} repos={repos.data} onClose={() => setPlan(null)} />}

      <div className="card">
        <div className="toolbar">
          <h3 style={{ margin: 0 }}>New releases</h3>
          {unread.length > 0 && <Badge kind="warn">{unread.length} unread</Badge>}
          <span className="muted">last check {when(st?.last_check)}{st?.not_checked_yet ? `, ${st.not_checked_yet} package${st.not_checked_yet === 1 ? "" : "s"} not checked yet` : ""}</span>
          <span className="toolbar-end">
            <label className="row" style={{ gap: 6 }} title="While devmem-app ui runs it checks on its own; `devmem-app releases check` does the same from cron. Set DEVMEM_RELEASE_CHECK_HOURS to pin it.">
              <span className="muted">Watch</span>
              <select value={s.interval_hours ?? 12} disabled={!st || s.from_environment} aria-label="Check interval" onChange={(e) => setSetting({ interval_hours: Number(e.target.value) })}>
                {HOURS.map(([h, l]) => <option key={h} value={h}>{l}</option>)}
              </select>
            </label>
            {unread.length > 0 && <button className="ghost" disabled={!!busy} onClick={() => run("read", () => api.post("/api/releases/read", {}).then(rel.reload))}>Mark all read</button>}
            <button className="ghost" onClick={() => setShowSettings((v) => !v)} aria-expanded={showSettings}><Icon name="key" />Settings</button>
          </span>
        </div>
        {showSettings && <Settings st={s} sp={sp} onSaved={() => { rel.reload(); search.reload(); }} onError={setErr} />}
        {!events.length ? <Empty>{st?.last_check ? "No new releases since devmem started watching. The first check records the current version of each library; later checks report anything newer." : "Not checked yet. The first check records the version of every library your repos use."}</Empty> : (
          <div className="libs">
            {events.slice(0, 30).map((e) => {
              const users = Object.entries(e.behind || {}).filter(([, b]) => b !== "none") as [string, string][];
              const sm = e.summary || {};
              return (
                <div className={`lib ${e.read ? "" : "unread"}`} key={e.id}>
                  <div className="lib-head">
                    <b>{e.name}</b><span className="muted">{e.eco}</span>
                    <span className="mono">{e.from}</span><span className="mz-arrow" aria-hidden="true">→</span><span className="mono">{e.to}</span>
                    <Badge kind={(LEVEL[e.level] || LEVEL.minor)[1]}>{(LEVEL[e.level] || LEVEL.minor)[0]}</Badge>
                    {sm.security?.length > 0 && <Badge kind="bad">security fixes</Badge>}
                    {sm.breaking?.length > 0 && <Badge kind="warn">{sm.counts?.breaking ?? sm.breaking.length} breaking</Badge>}
                    <span className="muted">{e.released ? `released ${e.released}` : `seen ${when(e.seen_at)}`}</span>
                    <span className="grow" />
                    {e.url && /^https?:/.test(e.url) && <a className="btn-link" href={e.url} target="_blank" rel="noreferrer">Release notes</a>}
                  </div>
                  {(sm.security?.length || sm.breaking?.length || sm.added?.length) ? (
                    <ul className="plain lib-news">
                      {[...(sm.security || []).map((x: string) => ["security", x]), ...(sm.breaking || []).slice(0, 4).map((x: string) => ["breaking", x]), ...(sm.added || []).slice(0, 3).map((x: string) => ["new", x])]
                        .map(([k, x], i) => <li key={i}><span className={`tag ${k === "new" ? "" : "warn"}`}>{k}</span> <span className="mono">{x.split(": ")[0]}</span> <Md text={x.split(": ").slice(1).join(": ")} /></li>)}
                    </ul>) : null}
                  <div className="lib-use">
                    {users.length ? <>
                      <span className="muted">Behind in:</span>
                      {users.map(([r, b]) => (
                        <span key={r} className="tag" title={`${r} uses ${e.repos[r]}`}>{r} <b>{e.repos[r]}</b> ({b})
                          <button className="ghost sm" disabled={!!busy} onClick={() => build(e.name, r, e.to)}>{busy === "plan:" + e.name ? "Preparing…" : "Prepare upgrade"}</button></span>))}
                    </> : <span className="muted">No onboarded repo is behind on it.</span>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="card">
        <div className="toolbar">
          <h3 style={{ margin: 0 }}>Behind in {repo || "this repo"}</h3>
          <span className="toolbar-end">
            <label className="row" title={sp?.ready === false ? "Search is not set up: open Settings above" : "Search the web for migration guides and breaking-change write-ups. Only the library name and versions are sent."}>
              <input type="checkbox" checked={web} onChange={(e) => setWeb(e.target.checked)} disabled={sp?.provider === "off"} />Search the web{sp ? ` (${sp.provider})` : ""}
            </label>
            {llm.data?.configured
              ? <label className="row" title="The model reads the change notes against this repo's call sites and suggests the concrete edits (sent: the notes and the lines that use the library)."><input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} />Model suggestions ({llm.data.model})</label>
              : <button className="ghost" onClick={() => nav("ai")} title="Optional: a model can turn the notes into concrete edits for this repo">Set up AI</button>}
            <button disabled={!repo || !!busy} onClick={refreshVersions}><span className={busy === "versions" ? "spin" : ""}><Icon name="refresh" /></span>Latest versions</button>
          </span>
        </div>
        {!repo ? <Empty>Pick a repo in the top bar.</Empty> : cands.loading && !libs.length ? <Empty>Loading…</Empty> : !libs.length
          ? <Empty>Every library {repo} declares is on its latest release (as of the last check). Press <b>Latest versions</b> to ask the registries again.</Empty> : (
          <table>
            <thead><tr><th>Library</th><th>In use</th><th>Latest</th><th>Upgrade</th><th className="num">Files</th><th className="num">Lines</th><th>Prompt</th><th></th></tr></thead>
            <tbody>{libs.map((l) => (
              <tr key={l.eco + l.name}>
                <td><b>{l.name}</b> <span className="muted">{l.eco}</span></td>
                <td className="mono">{l.using || <span className="muted">{l.spec || "unpinned"}</span>}</td>
                <td className="mono">{l.latest}</td>
                <td><Badge kind={(LEVEL[l.level] || LEVEL.minor)[1]}>{(LEVEL[l.level] || LEVEL.minor)[0]}</Badge></td>
                <td className="num">{l.files}</td>
                <td className="num">{l.sites}</td>
                <td>{l.plan ? <a onClick={() => open(l.name)} title={`prepared ${when(l.plan.ts)}`}>for {l.plan.to}: {l.plan.affected} change{l.plan.affected === 1 ? "" : "s"} touch the code</a> : <span className="muted">none yet</span>}</td>
                <td><button className={l.plan ? "ghost sm" : "sm"} disabled={!!busy} onClick={() => build(l.name)}>{busy === "plan:" + l.name ? "Preparing…" : l.plan ? "Rebuild" : "Build upgrade prompt"}</button></td>
              </tr>))}
            </tbody>
          </table>
        )}
        <p className="hint" style={{ margin: "12px 0 0" }}>Docs and release notes are read from each project's git repository at the release's tag (README, docs, migration guides, changelog), then its docs site; the registry only says which version is current. Set <code>GITHUB_TOKEN</code> to read GitHub release pages beyond the anonymous limit.</p>
      </div>
    </>
  );
}

function PlanView({ plan, repos, onClose }: { plan: any; repos: any; onClose: () => void }) {
  const [tab, setTab] = useState<"prompt" | "changes" | "sources">("prompt");
  const aff: any[] = plan.affected || [];
  return (
    <div className="card plan">
      <div className="toolbar">
        <h3 style={{ margin: 0 }}>Upgrade {plan.lib} <span className="mono">{plan.from || "?"}</span> → <span className="mono">{plan.to}</span> in {plan.repo}</h3>
        <Badge kind={(LEVEL[plan.level] || LEVEL.minor)[1]}>{(LEVEL[plan.level] || LEVEL.minor)[0]}</Badge>
        <span className="toolbar-end"><Copy text={plan.prompt} label="Copy prompt" /><button className="ghost" onClick={onClose}>Close</button></span>
      </div>
      <div className="kpis" style={{ marginBottom: 16 }}>
        <div className="lead"><span>Changes that touch this code</span><b>{aff.length}</b><small>matched by name to lines here</small></div>
        <div><span>Lines using {plan.lib}</span><b>{plan.usage?.sites ?? 0}</b><small>in {plan.usage?.files?.length ?? 0} files</small></div>
        <div><span>Release notes</span><b>{plan.notes?.versions?.length ?? 0}</b><small>{plan.notes?.source || plan.notes?.error || "none found"}</small></div>
        <div><span>Guides and web pages</span><b>{(plan.guides?.length || 0) + (plan.web?.pages?.length || 0)}</b><small>{plan.web?.error && plan.web.error !== "web search not requested" ? "web search failed" : `~${(plan.tokens || 0).toLocaleString()} token prompt`}</small></div>
      </div>
      <div className="seg" role="tablist" style={{ marginBottom: 12 }}>
        {(["prompt", "changes", "sources"] as const).map((k) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{{ prompt: "Prompt for the agent", changes: `Affected code (${aff.length})`, sources: "Where it came from" }[k]}</button>)}
      </div>
      {tab === "prompt" && <pre className="code prompt-text">{plan.prompt}</pre>}
      {tab === "changes" && (!aff.length ? <Empty>No change note names something this repo uses. The prompt still lists the other breaking changes to check.</Empty> : (
        <div className="aff-list">
          {aff.map((c: any, i: number) => (
            <div className="aff" key={i}>
              <div>
                <div className="tags"><Badge kind={c.kind === "deprecated" ? "" : "warn"}>{c.kind}</Badge><span className="tag">{c.version}</span><span className="tag">{c.source}</span>{c.hits.map((h: string) => <span className="tag mono" key={h}>{h}</span>)}</div>
                <p style={{ margin: "6px 0" }}><Md text={c.text} /></p>
                {c.sites.map((s: any) => (
                  <div className="aff-site" key={s.file + s.line}><span className="mono muted">{s.file}:{s.line}</span><code>{s.text}</code><EditorLink href={editorHref(repos, plan.repo, s.file, s.line)} /></div>))}
                {c.site_count > c.sites.length && <div className="hint">…and {c.site_count - c.sites.length} more line(s)</div>}
              </div>
            </div>))}
        </div>))}
      {tab === "sources" && (
        <ul className="plain sources">
          {plan.notes?.url && <li><b>Release notes</b>: <a href={plan.notes.url} target="_blank" rel="noreferrer">{plan.notes.url}</a> ({plan.notes.source}; {plan.notes.versions.join(", ")})</li>}
          {plan.notes?.error && <li className="muted">Release notes: {plan.notes.error}</li>}
          {(plan.guides || []).map((g: any) => <li key={g.url}><b>Migration guide</b>: <a href={g.url} target="_blank" rel="noreferrer">{g.title}</a></li>)}
          {(plan.web?.pages || []).map((p: any) => <li key={p.url}><b>Web</b>: <a href={p.url} target="_blank" rel="noreferrer">{p.title}</a></li>)}
          {plan.web?.queries?.length > 0 && <li className="muted">Searched: {plan.web.queries.map((q: string) => `“${q}”`).join(", ")}{plan.web.error ? `: ${plan.web.error}` : ""}</li>}
          {plan.docs_note && <li className="muted">Docs: {plan.docs_note}</li>}
          {plan.repo_url && <li><b>Repository</b>: <a href={plan.repo_url} target="_blank" rel="noreferrer">{plan.repo_url}</a></li>}
          {plan.ai_error && <li className="muted">Model: {plan.ai_error}</li>}
        </ul>)}
    </div>
  );
}

function Settings({ st, sp, onSaved, onError }: { st: any; sp: any; onSaved: () => void; onError: (m: string) => void }) {
  const [hook, setHook] = useState(st.webhook || "");
  const [prov, setProv] = useState(sp?.provider || "duckduckgo");
  const [url, setUrl] = useState(sp?.url || "");
  const [cx, setCx] = useState(sp?.cx || "");
  const [key, setKey] = useState("");
  const [test, setTest] = useState("");
  useEffect(() => { if (sp) { setProv(sp.provider); setUrl(sp.url || ""); setCx(sp.cx || ""); } }, [sp?.provider]); // eslint-disable-line
  const needs: string[] = (sp?.providers || []).find((p: any) => p.id === prov)?.needs || [];
  const save = async () => {
    try {
      await api.put("/api/releases/settings", { webhook: hook });
      await api.put("/api/settings/search", { provider: prov, url, cx, api_key: key });
      setKey(""); setTest(""); onSaved();
    } catch (e: any) { onError(e.message); }
  };
  const tryIt = async () => { setTest("Searching…"); try { const r = await api.post("/api/settings/search/test"); setTest(`Works: ${r.results.length} result(s), e.g. ${r.results[0]?.title || "-"}`); } catch (e: any) { setTest(e.message); } };
  return (
    <div className="settings-grid">
      <div>
        <h4>Web search for migration guides</h4>
        <select value={prov} onChange={(e) => setProv(e.target.value)} disabled={sp?.from_environment} aria-label="Search provider">
          {(sp?.providers || []).map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        {needs.includes("url") && <input placeholder="https://searx.example.org" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="SearXNG address" />}
        {needs.includes("api_key") && <input type="password" placeholder={sp?.key_set ? "key saved (type to replace)" : "API key"} value={key} onChange={(e) => setKey(e.target.value)} aria-label="Search API key" />}
        {needs.includes("cx") && <input placeholder="Search engine ID (cx)" value={cx} onChange={(e) => setCx(e.target.value)} aria-label="Google search engine ID" />}
        <p className="hint">Only the library name and the two versions are sent. DuckDuckGo needs no key but sometimes refuses automated queries; SearXNG, Brave, Tavily or Google are dependable. Environment: <code>DEVMEM_SEARCH</code>, <code>DEVMEM_SEARXNG_URL</code>, <code>BRAVE_API_KEY</code>, <code>TAVILY_API_KEY</code>, <code>GOOGLE_CSE_KEY</code> + <code>GOOGLE_CSE_ID</code>.</p>
        <div className="row"><button className="ghost sm" onClick={tryIt}>Test search</button>{test && <span className="muted">{test}</span>}</div>
      </div>
      <div>
        <h4>When a new release is found</h4>
        <label className="row"><input type="checkbox" checked={!!st.refresh_docs} onChange={(e) => api.put("/api/releases/settings", { refresh_docs: e.target.checked }).then(onSaved).catch((x) => onError(x.message))} />Fetch the new version's docs (and update published repos)</label>
        <label className="row"><input type="checkbox" checked={!!st.auto_plan} onChange={(e) => api.put("/api/releases/settings", { auto_plan: e.target.checked }).then(onSaved).catch((x) => onError(x.message))} />Prepare an upgrade prompt for each repo that is behind (no web search)</label>
        <input placeholder="Webhook URL (Slack, Teams, Discord…), optional" value={hook} onChange={(e) => setHook(e.target.value)} disabled={st.from_environment && !!st.webhook} aria-label="Webhook URL" />
        <p className="hint">One message per check that found new releases: what was released, how big a jump it is and which repos use an older version.</p>
      </div>
      <div className="settings-save"><button className="primary" onClick={save}><Icon name="check" />Save settings</button></div>
    </div>
  );
}
