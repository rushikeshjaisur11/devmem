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

const PAGES: [string, string, any][] = [
  ["repos", "Repos", Repos], ["explorer", "Explorer", Explorer], ["impact", "Impact", Impact], ["graph", "Graph", Graph],
  ["lineage", "Lineage", Lineage], ["hygiene", "Hygiene", Hygiene], ["memory", "Memory", Memory], ["share", "Share", Share],
  ["chat", "Chat", Chat], ["metrics", "Metrics", Metrics],
];

export default function App() {
  const { page, params } = useRoute();
  const Page = (PAGES.find((p) => p[0] === page) || PAGES[0])[2];
  return (
    <div className="app">
      <nav className="side" aria-label="Main">
        <h1>devmem</h1>
        {PAGES.map(([id, label]) => (
          <a key={id} className={page === id ? "on" : ""} onClick={() => nav(id)} href={`#/${id}`}>{label}</a>
        ))}
      </nav>
      <main><ErrorBoundary key={page + JSON.stringify(params)}><Page params={params} /></ErrorBoundary></main>
    </div>
  );
}
