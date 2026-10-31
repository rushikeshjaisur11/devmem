import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useRoute, nav } from "./route";
import { EDITORS, ErrorBoundary, getEditor, useRepos } from "./components";
const Overview = lazy(() => import("./pages/Overview"));
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

const ic = (d: string) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: d }} />;
const ICONS: Record<string, string> = {
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
};
const GROUPS: [string, string[]][] = [["Workspace", ["overview", "repos"]], ["Understand code", ["impact", "graph", "lineage"]], ["Guide agents", ["chat", "memory", "docs", "hub"]], ["Quality", ["hygiene", "metrics"]]];
const PAGES: [string, string, any][] = [
  ["overview", "Overview", Overview], ["repos", "Repos", Repos], ["impact", "Impact", Impact], ["graph", "Graph", Graph],
  ["lineage", "Lineage", Lineage], ["hygiene", "Hygiene", Hygiene], ["memory", "Lessons & rules", Memory], ["docs", "Library docs", Docs], ["hub", "Knowledge hub", Hub],
  ["chat", "Chat", Chat], ["metrics", "Metrics", Metrics],
];
const LABEL = Object.fromEntries(PAGES.map((p) => [p[0], p[1]]));

/** The mark: concentric blast-radius rings around a highlighted symbol. */
const Mark = () => (
  <svg viewBox="0 0 34 34" aria-hidden="true">
    <circle cx="17" cy="17" r="15" fill="none" stroke="currentColor" strokeWidth="1.6" strokeDasharray="2.5 3" opacity=".55" />
    <circle cx="17" cy="17" r="10" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="17" cy="17" r="5" fill="var(--hl)" stroke="currentColor" strokeWidth="1.8" />
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
  const [editor, setEditor] = useState(getEditor);
  const pickEditor = (v: string) => { try { localStorage.setItem("devmem-editor", v); } catch { /* private mode */ } setEditor(v); };
  useEffect(() => {
    const f = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); } };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);
  const Page = (PAGES.find((p) => p[0] === page) || PAGES[0])[2];
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  return (
    <div className="app">
      <nav className="side" aria-label="Main">
        <div className="brand">
          <div className="logo"><Mark /></div>
          <div><h1>devmem</h1><small>memory for coding agents</small></div>
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
          <select value={editor} onChange={(e) => pickEditor(e.target.value)} aria-label="Open files in" title="Open files in">
            {Object.entries(EDITORS).map(([k, [label]]) => <option key={k} value={k}>{k === "none" ? "Editor links off" : label}</option>)}
          </select>
          <button className="icon-btn" onClick={toggle} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"} title={dark ? "Light theme" : "Dark theme"}>
            {dark ? ic('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>') : ic('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>')}
          </button>
        </div>
      </nav>
      <div style={{ minWidth: 0 }}>
        <header className="topbar">
          <span className="crumb">devmem / <b>{LABEL[page] || "Overview"}</b>{params.repo ? <> / {params.repo}</> : null}</span>
          <button className="kbtn" onClick={() => setPalette(true)} aria-label="Go to a page or repo">{ic('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>')}Go to…<kbd>{mac ? "⌘" : "Ctrl"} K</kbd></button>
        </header>
        <main><ErrorBoundary key={page + JSON.stringify(params) + editor}><Suspense fallback={<p className="muted">Loading…</p>}><Page params={params} /></Suspense></ErrorBoundary></main>
      </div>
      {palette && <Palette onClose={() => setPalette(false)} />}
    </div>
  );
}
