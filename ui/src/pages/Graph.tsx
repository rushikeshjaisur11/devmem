import { useState } from "react";
import { api, q } from "../api";
import { CyGraph, Empty, Err, RepoSelect, useAsync, useRepos } from "../components";
import { nav } from "../route";

export default function Graph({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const [focus, setFocus] = useState(params.focus || "");
  const [depth, setDepth] = useState(1);
  const [sel, setSel] = useState<any>(null);
  const overview = useAsync(() => (focus ? Promise.resolve(null) : api.get("/api/graph/overview")), [focus]);
  const focused = useAsync(() => (focus && repo ? api.get(`/api/repos/${repo}/graph?${q({ focus, depth })}`) : Promise.resolve(null)), [focus, repo, depth]);
  const g = focus ? focused.data : overview.data;
  return (
    <>
      <div className="row" style={{ marginBottom: 6 }}>
        <h2 style={{ margin: 0 }}>Graph</h2>
        {focus && repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}
        {focus && <><label>Depth <select value={depth} onChange={(e) => setDepth(+e.target.value)}>{[1, 2, 3].map((d) => <option key={d}>{d}</option>)}</select></label><button onClick={() => { setFocus(""); setSel(null); }}>Back to overview</button></>}
      </div>
      <p className="sub">{focus ? `Neighbourhood of ${focus.split("#")[1] || focus}: calls and tests, click a node to inspect.` : "All onboarded repos: modules inside each repo, and tables shared between repos (orange diamonds)."}</p>
      <Err msg={overview.error || focused.error} />
      <div className="legend">
        {focus ? <><span><i style={{ background: "var(--accent)" }} />focus</span><span><i style={{ background: "var(--surface-2)", border: "1px solid var(--good)" }} />test</span></> :
          <><span><i style={{ background: "var(--warn)" }} />shared table</span><span>solid arrows: calls across modules</span><span>orange: writes · dashed blue: reads</span></>}
      </div>
      {!g ? <Empty>Loading…</Empty> : g.nodes.length === 0 ? <Empty>Nothing to draw yet: onboard and index a repo first.</Empty> :
        <CyGraph elements={[...g.nodes, ...g.edges]} layout={focus ? "concentric" : "cose"} onSelect={setSel} />}
      {sel && (
        <div className="card" style={{ marginTop: 10 }}>
          <b>{sel.label}</b> <span className="muted">{sel.kind}</span>
          {sel.file && <div className="mono">{sel.file}</div>}
          <div className="row" style={{ marginTop: 8 }}>
            {focus && sel.id.includes("#") && <><button onClick={() => { setFocus(sel.id); setSel(null); }}>Re-center here</button><button onClick={() => nav("explorer", { repo, symbol: sel.id })}>Open in Explorer</button><button onClick={() => nav("impact", { repo, target: sel.id })}>Impact</button></>}
            {sel.kind === "table" && <button onClick={() => nav("lineage", { table: sel.label })}>Lineage</button>}
          </div>
        </div>
      )}
    </>
  );
}
