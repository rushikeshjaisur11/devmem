import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useRoute, nav } from "./route";
import { api } from "./api";
import { EDITORS, ErrorBoundary, getEditor, Icon, RepoContext, useConfig, useRepos } from "./components";
const Overview = lazy(() => import("./pages/Overview"));
const Agents = lazy(() => import("./pages/Agents"));
const Repos = lazy(() => import("./pages/Repos"));
const Impact = lazy(() => import("./pages/Impact"));
const Hygiene = lazy(() => import("./pages/Hygiene"));
const Lineage = lazy(() => import("./pages/Lineage"));
const Memory = lazy(() => import("./pages/Memory"));
const Graph = lazy(() => import("./pages/Graph"));
const Metrics = lazy(() => import("./pages/Metrics"));
const Chat = lazy(() => import("./pages/Chat"));
const Hub = lazy(() => import("./pages/Hub"));
const Docs = lazy(() => import("./pages/Docs"));
const Reuse = lazy(() => import("./pages/Reuse"));
const Ai = lazy(() => import("./pages/Ai"));
const Definitions = lazy(() => import("./pages/Definitions"));
const Analysis = lazy(() => import("./pages/Analysis"));
const Integrations = lazy(() => import("./pages/Integrations"));
const Modernize = lazy(() => import("./pages/Modernize"));

const ic = (d: string) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: d }} />;
const ICONS: Record<string, string> = {
  agents: '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 3v4M9 12h.01M15 12h.01M9 16h6"/>',
  overview: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/>',
  docs: '<path d="M4 4h12a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4z"/><path d="M8 9h8M8 13h6"/>',
  hub: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  repos: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  impact: '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
  graph: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8 7l8 1M7.5 8l3.5 8M16.5 10.2 13.2 16"/>',
  lineage: '<path d="M4 6h5a3 3 0 0 1 3 3v6a3 3 0 0 0 3 3h5"/><path d="m17 15 3 3-3 3"/><circle cx="4" cy="6" r="1"/>',
  hygiene: '<path d="M12 3 4 6v6c0 4.5 3.2 7.8 8 9 4.8-1.2 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  memory: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  metrics: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  ai: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
  modernize: '<path d="M4 17 10 11l4 4 6-7"/><path d="M15 8h5v5"/>',
  integrations: '<path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0zM12 16v5"/>',
  analysis: '<path d="M10 3 8 9l-6 2 6 2 2 6 2-6 6-2-6-2z"/><path d="M19 15v5M16.5 17.5h5"/>',
  definitions: '<path d="M4 5h16M4 10h10M4 15h7"/><path d="m15 17 2 2 4-5"/>',
  reuse: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
};
const GROUPS: [string, string[]][] = [["Start here", ["overview", "agents", "repos"]], ["Before you change code", ["reuse", "impact", "graph", "lineage"]], ["Guide agents", ["chat", "memory", "analysis", "definitions", "docs", "hub"]], ["Health", ["hygiene", "modernize", "metrics"]], ["Settings", ["ai", "integrations"]]];
const PAGES: [string, string, any][] = [
  ["overview", "Overview", Overview], ["agents", "Agents", Agents], ["repos", "Repos", Repos], ["impact", "What breaks if…", Impact], ["graph", "Code map", Graph], ["reuse", "Already built elsewhere", Reuse],
  ["lineage", "Data flow", Lineage], ["hygiene", "Code health", Hygiene], ["memory", "Lessons & rules", Memory], ["docs", "Library docs", Docs], ["hub", "Team hub", Hub],
  ["chat", "Ask & plan", Chat], ["metrics", "Results", Metrics], ["analysis", "AI analysis", Analysis], ["definitions", "Improve descriptions", Definitions], ["ai", "AI setup", Ai], ["integrations", "Integrations", Integrations], ["modernize", "Modernize", Modernize],
];
const LABEL = Object.fromEntries(PAGES.map((p) => [p[0], p[1]]));
// pages that show one repo at a time: the top bar offers the repo switcher on these
const REPO_PAGES = new Set(["agents", "impact", "graph", "reuse", "hygiene", "memory", "metrics", "definitions", "analysis", "docs", "modernize"]);

/** The mark: a symbol and the blast radius around it, drawn in the one accent. */
const Mark = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="10.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2 2.6" />
    <circle cx="12" cy="12" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
    <circle cx="12" cy="12" r="3" fill="currentColor" />
  </svg>
);

function useTheme() {
  const get = () => { try { return localStorage.getItem("devmem-theme") || ""; } catch { return ""; } };
  const [t, setT] = useState(get);
  useEffect(() => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }, [t]);
  const dark = t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  const toggle = () => { const n = dark ? "light" : "dark"; try { localStorage.setItem("devmem-theme", n); } catch { /* private mode */ } setT(n); };
  return { dark, toggle };
}

/** Shown instead of the app when a deployed server has not seen this browser's access token yet. */
function SignIn({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true); setErr("");
    try { await api.post("/api/login", { token }); onDone(); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <div className="signin">
      <form className="card" onSubmit={(e) => { e.preventDefault(); if (token.trim()) go(); }}>
        <div className="brand"><div className="logo"><Mark /></div><span className="name">devmem</span></div>
        <h1>Sign in</h1>
        <p className="sub">This devmem runs on a server. Enter its access token: the value of <code>DEVMEM_TOKEN</code>, or the token printed when the server started.</p>
        <label className="field">Access token<input type="password" autoFocus autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} /></label>
        {err && <div className="err" role="alert">{err}</div>}
        <button className="primary" type="submit" disabled={busy || !token.trim()} style={{ marginTop: 16, width: "100%" }}><Icon name="key" />{busy ? "Checking…" : "Sign in"}</button>
      </form>
    </div>
  );
}

/** Ctrl/Cmd+K: jump to any page, or check impact in a repo. */
function Palette({ onClose }: { onClose: () => void }) {
  const repos = useRepos();
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const items: { label: string; hint: string; icon: string; go: () => void }[] = [
    ...PAGES.map(([id, label]) => ({ label, hint: "page", icon: id, go: () => nav(id) })),
    ...(repos.data || []).filter((r) => r.indexed).map((r) => ({ label: r.name, hint: "check impact", icon: "repos", go: () => nav("impact", { repo: r.name }) })),
  ].filter((x) => x.label.toLowerCase().includes(q.trim().toLowerCase()));
  const pick = (n: number) => { if (items[n]) { items[n].go(); onClose(); } };
  return (
    <div className="palette-bg" onMouseDown={onClose}>
      <div className="palette" role="dialog" aria-label="Go to" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={ref} value={q} placeholder="Go to a page or repo" aria-label="Go to"
          onChange={(e) => { setQ(e.target.value); setI(0); }}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            else if (e.key === "ArrowDown") { e.preventDefault(); setI(Math.min(i + 1, items.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setI(Math.max(i - 1, 0)); }
            else if (e.key === "Enter") pick(i);
          }} />
        {items.length ? (
          <ul role="listbox">
            {items.map((x, n) => <li key={x.hint + x.label} role="option" aria-selected={n === i} className={n === i ? "on" : ""} onMouseEnter={() => setI(n)} onClick={() => pick(n)}>{ic(ICONS[x.icon])}{x.label}<small>{x.hint}</small></li>)}
          </ul>
        ) : <div className="none">Nothing called “{q}”. Try a page name like “impact”, or a repo name.</div>}
      </div>
    </div>
  );
}

export default function App() {
  const { page, params } = useRoute();
  const { dark, toggle } = useTheme();
  const [palette, setPalette] = useState(false);
  const [menu, setMenu] = useState(false);
  const repos = useRepos();
  const cfg = useConfig();
  const [locked, setLocked] = useState(false);
  // one current repo for the whole app: from the link (?repo=), else the last one used, else the first indexed
  const [repo, setRepoState] = useState(() => { try { return params.repo || localStorage.getItem("devmem-repo") || ""; } catch { return params.repo || ""; } });
  const setRepo = (v: string) => { setRepoState(v); try { localStorage.setItem("devmem-repo", v); } catch { /* private mode */ } if (params.repo && params.repo !== v) nav(page, { ...params, repo: v }); };
  useEffect(() => { if (params.repo && params.repo !== repo) setRepoState(params.repo); }, [params.repo]); // eslint-disable-line
  const indexed = (repos.data || []).filter((r) => r.indexed);
  useEffect(() => { if (repos.data && indexed.length && !indexed.some((r) => r.name === repo)) setRepoState(indexed[0].name); }, [repos.data]); // eslint-disable-line
  const [edMenu, setEdMenu] = useState(false);
  useEffect(() => { if (!edMenu) return; const f = () => setEdMenu(false); window.addEventListener("click", f); return () => window.removeEventListener("click", f); }, [edMenu]);
  useEffect(() => { if (cfg.data) setLocked(!cfg.data.signed_in); }, [cfg.data]);
  useEffect(() => { const f = () => setLocked(true); window.addEventListener("devmem-signin", f); return () => window.removeEventListener("devmem-signin", f); }, []);
  useEffect(() => setMenu(false), [page, params.repo]);
  const [editor, setEditor] = useState(getEditor);
  const pickEditor = (v: string) => { try { localStorage.setItem("devmem-editor", v); } catch { /* private mode */ } setEditor(v); };
  useEffect(() => {
    const f = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); } };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);
  const Page = (PAGES.find((p) => p[0] === page) || PAGES[0])[2];
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  if (locked) return <SignIn onDone={() => window.location.reload()} />;
  const deployed = !!cfg.data?.deployed;
  return (
    <div className={`app ${menu ? "menu-open" : ""}`}>
      <nav className="side" aria-label="Main">
        <div className="brand">
          <div className="logo"><Mark /></div>
          <span className="name">devmem</span><span className="scope">{(repos.data || []).length} repos</span>
        </div>
        {GROUPS.map(([g, ids]) => (
          <div key={g} style={{ display: "contents" }}>
            <div className="group">{g}</div>
            {ids.map((id) => (
              <a key={id} className={page === id ? "on" : ""} href={`#/${id}`} aria-current={page === id ? "page" : undefined}>{ic(ICONS[id])}<span>{LABEL[id]}</span></a>
            ))}
          </div>
        ))}
        <div className="side-foot">
          {deployed
            ? <button className="ghost sm" onClick={() => api.post("/api/logout").then(() => window.location.reload())}><Icon name="out" />Sign out</button>
            : <span className="menu-wrap" onClick={(e) => e.stopPropagation()}>
                <button className="ghost sm foot-btn" aria-haspopup="menu" aria-expanded={edMenu} onClick={() => setEdMenu((o) => !o)} title="File and line links on every page open in this editor">
                  <Icon name="code" /><span>{editor === "none" ? "Editor links off" : `Opens in ${EDITORS[editor]?.[0] || "VS Code"}`}</span>
                </button>
                {edMenu && (
                  <div className="menu up" role="menu">
                    {Object.entries(EDITORS).map(([k, [label]]) => (
                      <button key={k} role="menuitemradio" aria-checked={editor === k} onClick={() => { pickEditor(k); setEdMenu(false); }}>
                        <Icon name={editor === k ? "check" : "blank"} /><span>{k === "none" ? "Do not link files" : `Open files in ${label}`}</span>
                      </button>
                    ))}
                  </div>
                )}
              </span>}
          <button className="icon-btn" onClick={toggle} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"} title={dark ? "Light theme" : "Dark theme"}>
            {dark ? ic('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>') : ic('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>')}
          </button>
        </div>
      </nav>
      <header className="topbar">
        <button className="icon-btn menu-btn" onClick={() => setMenu((m) => !m)} aria-label={menu ? "Close menu" : "Open menu"} aria-expanded={menu}>{ic(menu ? '<path d="M6 6l12 12M18 6 6 18"/>' : '<path d="M4 7h16M4 12h16M4 17h16"/>')}</button>
        <span className="crumb"><b>{LABEL[page] || "Overview"}</b></span>
        {REPO_PAGES.has(page) && indexed.length > 0 && (
          <label className="repo-switch" title="The repo this page shows. It stays the same as you move between pages.">
            <Icon name="folder" />
            <select value={repo} onChange={(e) => setRepo(e.target.value)} aria-label="Repository">
              {(page === "memory" ? repos.data || [] : indexed).map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
            </select>
          </label>
        )}
        <button className="kbtn" onClick={() => setPalette(true)} aria-label="Go to a page or repo">{ic('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>')}<span>Go to a page or repo</span><kbd>{mac ? "⌘" : "Ctrl"} K</kbd></button>
        <span className="spacer" />
      </header>
      <div className="sheet">
        <main><RepoContext.Provider value={{ repo, setRepo, repos: repos.data }}><ErrorBoundary key={page + JSON.stringify(params) + editor + repo}><Suspense fallback={<p className="muted">Loading…</p>}><Page params={params} /></Suspense></ErrorBoundary></RepoContext.Provider></main>
      </div>
      {palette && <Palette onClose={() => setPalette(false)} />}
    </div>
  );
}
