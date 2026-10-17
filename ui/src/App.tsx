import { useEffect, useState } from "react";
import { useRoute, nav } from "./route";
import { ErrorBoundary } from "./components";
import Repos from "./pages/Repos";
import Explorer from "./pages/Explorer";
import Impact from "./pages/Impact";
import Hygiene from "./pages/Hygiene";
import Lineage from "./pages/Lineage";
import Memory from "./pages/Memory";
import Share from "./pages/Share";
import Graph from "./pages/Graph";
import Metrics from "./pages/Metrics";
import Chat from "./pages/Chat";
import Reuse from "./pages/Reuse";

const ic = (d: string) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: d }} />;
const ICONS: Record<string, string> = {
  reuse: '<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>',
  repos: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  explorer: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  impact: '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
  graph: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8 7l8 1M7.5 8l3.5 8M16.5 10.2 13.2 16"/>',
  lineage: '<path d="M4 6h5a3 3 0 0 1 3 3v6a3 3 0 0 0 3 3h5"/><path d="m17 15 3 3-3 3"/><circle cx="4" cy="6" r="1"/>',
  hygiene: '<path d="M12 3 4 6v6c0 4.5 3.2 7.8 8 9 4.8-1.2 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  memory: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/>',
  share: '<circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="m8.2 10.9 7.6-4.3M8.2 13.1l7.6 4.3"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  metrics: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
};
const GROUPS: [string, string[]][] = [["Explore", ["repos", "explorer", "reuse", "impact", "graph", "lineage"]], ["Quality", ["hygiene", "memory", "metrics"]], ["Collaborate", ["share", "chat"]]];
const PAGES: [string, string, any][] = [
  ["repos", "Repos", Repos], ["explorer", "Explorer", Explorer], ["reuse", "Reuse", Reuse], ["impact", "Impact", Impact], ["graph", "Graph", Graph],
  ["lineage", "Lineage", Lineage], ["hygiene", "Hygiene", Hygiene], ["memory", "Memory", Memory], ["share", "Share", Share],
  ["chat", "Chat", Chat], ["metrics", "Metrics", Metrics],
];

function useTheme() {
  const get = () => { try { return localStorage.getItem("devmem-theme") || ""; } catch { return ""; } };
  const [t, setT] = useState(get);
  useEffect(() => { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }, [t]);
  const dark = t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  const toggle = () => { const n = dark ? "light" : "dark"; try { localStorage.setItem("devmem-theme", n); } catch { /* private mode */ } setT(n); };
  return { dark, toggle };
}

export default function App() {
  const { page, params } = useRoute();
  const { dark, toggle } = useTheme();
  const Page = (PAGES.find((p) => p[0] === page) || PAGES[0])[2];
  const label = Object.fromEntries(PAGES.map((p) => [p[0], p[1]]));
  return (
    <div className="app">
      <nav className="side" aria-label="Main">
        <div className="brand">
          <div className="logo">{ic('<path d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z"/><path d="M4 7v5c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12v5c0 1.7 3.6 3 8 3s8-1.3 8-3v-5"/>')}</div>
          <div><h1>devmem</h1><small>code knowledge</small></div>
        </div>
        {GROUPS.map(([g, ids]) => (
          <div key={g} style={{ display: "contents" }}>
            <div className="group">{g}</div>
            {ids.map((id) => (
              <a key={id} className={page === id ? "on" : ""} onClick={() => nav(id)} href={`#/${id}`} aria-current={page === id ? "page" : undefined}>{ic(ICONS[id])}{label[id]}</a>
            ))}
          </div>
        ))}
        <div className="side-foot">
          <span>v0.1</span>
          <button className="icon-btn" onClick={toggle} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"} title="Toggle theme">
            {dark ? ic('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>') : ic('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>')}
          </button>
        </div>
      </nav>
      <main><ErrorBoundary key={page + JSON.stringify(params)}><Page params={params} /></ErrorBoundary></main>
    </div>
  );
}
