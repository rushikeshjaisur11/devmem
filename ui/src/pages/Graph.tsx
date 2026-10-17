import { useEffect, useMemo, useRef, useState } from "react";
import type cytoscape from "cytoscape";
import { api, q } from "../api";
import { CyGraph, EDGE_KINDS, Empty, Err, LAYOUT_LABELS, NODE_KINDS, RepoSelect, kindColor, useAsync, useRepos } from "../components";
import { nav } from "../route";

const SearchIcon = () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>;

export default function Graph({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const [focus, setFocus] = useState(params.focus || "");
  const [depth, setDepth] = useState(2);
  const [sel, setSel] = useState<any>(null);
  const [layout, setLayout] = useState("");
  const [query, setQuery] = useState("");
  const [hide, setHide] = useState<string[]>([]);
  const [hideRepos, setHideRepos] = useState<string[]>([]);
  const [edgeLabels, setEdgeLabels] = useState(true);
  const [find, setFind] = useState("");
  const [hits, setHits] = useState<any[]>([]);
  const cyRef = useRef<cytoscape.Core | null>(null);

  const overview = useAsync(() => (focus ? Promise.resolve(null) : api.get("/api/graph/overview")), [focus]);
  const focused = useAsync(() => (focus && repo ? api.get(`/api/repos/${repo}/graph?${q({ focus, depth })}`) : Promise.resolve(null)), [focus, repo, depth]);
  const g = focus ? focused.data : overview.data;
  const lay = layout || (focus ? "columns" : "cose");

  useEffect(() => { setSel(null); setHide([]); setHideRepos([]); setQuery(""); setLayout(""); }, [focus, repo]);
  useEffect(() => {
    if (!repo || find.trim().length < 2) { setHits([]); return; }
    const t = setTimeout(() => api.get(`/api/repos/${repo}/search?${q({ q: find })}`).then(setHits).catch(() => setHits([])), 200);
    return () => clearTimeout(t);
  }, [repo, find]);

  const elements = useMemo(() => (g ? [...g.nodes, ...g.edges] : []), [g]);
  const counts = useMemo(() => {
    const nk: Record<string, number> = {}, ek: Record<string, number> = {}, rp: string[] = [];
    (g?.nodes || []).forEach((n: any) => { nk[n.data.kind] = (nk[n.data.kind] || 0) + 1; if (n.data.kind === "repo") rp.push(n.data.label); });
    (g?.edges || []).forEach((e: any) => { ek[e.data.kind] = (ek[e.data.kind] || 0) + 1; });
    return { nk, ek, rp };
  }, [g]);
  const label = useMemo(() => Object.fromEntries((g?.nodes || []).map((n: any) => [n.data.id, n.data.label])), [g]);
  const links = useMemo(() => {
    if (!sel || !g) return { out: [], inc: [] };
    const m = (e: any, end: string) => ({ id: e.data[end], name: label[e.data[end]] || e.data[end], kind: e.data.kind, conf: e.data.conf });
    return { out: g.edges.filter((e: any) => e.data.source === sel.id).map((e: any) => m(e, "target")), inc: g.edges.filter((e: any) => e.data.target === sel.id).map((e: any) => m(e, "source")) };
  }, [sel, g, label]);

  const toggle = (arr: string[], set: (v: string[]) => void, k: string) => set(arr.includes(k) ? arr.filter((x) => x !== k) : [...arr, k]);
  const zoom = (f: number) => { const cy = cyRef.current; if (cy) cy.animate({ zoom: { level: cy.zoom() * f, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }, duration: 150 }); };
  const fit = () => cyRef.current?.animate({ fit: { eles: cyRef.current.elements(":visible"), padding: 30 }, duration: 200 });
  const exportPng = () => {
    const cy = cyRef.current; if (!cy) return;
    const a = document.createElement("a");
    a.href = cy.png({ full: true, scale: 2, bg: getComputedStyle(document.documentElement).getPropertyValue("--surface").trim() });
    a.download = `devmem-graph${focus ? "-focus" : ""}.png`; a.click();
  };
  const select = (id: string) => { const cy = cyRef.current; const n = cy?.getElementById(id); if (n && n.length) { cy!.elements().unselect(); n.select(); setSel(n.data()); cy!.animate({ center: { eles: n }, duration: 200 }); } };
  const symbolSel = sel && typeof sel.id === "string" && sel.id.includes("#");

  return (
    <>
      <h2>Graph</h2>
      <p className="sub">{focus ? <>Neighbourhood of <b>{focus.split("#")[1] || focus}</b>: who calls it, what it calls, and the tests that cover it.</> : "How your repos fit together: modules inside each repo, calls between them, and tables that link repos. Search a symbol to zoom into its call neighbourhood."}</p>

      <div className="gtools">
        {repos.data && <RepoSelect value={repo} onChange={(v) => { setRepo(v); }} repos={repos.data} />}
        <div className="search">
          <SearchIcon />
          <input placeholder="Find a symbol to focus on (e.g. retry)…" value={find} onChange={(e) => setFind(e.target.value)} aria-label="Find a symbol to focus on" />
          {hits.length > 0 && find && (
            <div className="hits">
              {hits.map((h) => <div key={h.id} onClick={() => { setFocus(h.id); setFind(""); setHits([]); }}><span>{h.qualname}</span><span className="muted mono">{h.file}</span></div>)}
            </div>
          )}
        </div>
        <div className="search" style={{ maxWidth: 260 }}>
          <SearchIcon />
          <input placeholder="Highlight on graph…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Highlight nodes on the graph" />
        </div>
        {focus && <label className="row" style={{ gap: 6 }}>Depth <select value={depth} onChange={(e) => setDepth(+e.target.value)}>{[1, 2, 3].map((d) => <option key={d}>{d}</option>)}</select></label>}
        <select value={lay} onChange={(e) => setLayout(e.target.value)} aria-label="Layout" title="Layout">{Object.entries(LAYOUT_LABELS).filter(([k]) => focus || k !== "columns").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        {focus && <button onClick={() => setFocus("")}>← Overview</button>}
      </div>

      <div className="card" style={{ padding: "12px 16px" }}>
        <div className="chips">
          <span className="muted" style={{ fontSize: 12, fontWeight: 600, marginRight: 4 }}>SHOW</span>
          {Object.entries(counts.nk).map(([k, n]) => (
            <button key={k} className={`chip ${hide.includes(k) ? "off" : ""}`} onClick={() => toggle(hide, setHide, k)} aria-pressed={!hide.includes(k)}>
              <i style={{ background: kindColor(k) }} />{(NODE_KINDS[k] || { label: k }).label}<span className="n">{n}</span>
            </button>
          ))}
          <span style={{ width: 10 }} />
          {Object.entries(counts.ek).map(([k, n]) => {
            const ek = EDGE_KINDS[k] || { label: k, color: "--text-3" };
            return (
              <button key={k} className={`chip ${hide.includes("edge:" + k) ? "off" : ""}`} onClick={() => toggle(hide, setHide, "edge:" + k)} aria-pressed={!hide.includes("edge:" + k)}>
                <i className={ek.dashed ? "dash" : "line"} style={{ borderColor: `var(${ek.color})` }} />{ek.label}<span className="n">{n}</span>
              </button>
            );
          })}
          {counts.rp.length > 1 && <>
            <span style={{ width: 10 }} />
            {counts.rp.map((r) => (
              <button key={r} className={`chip ${hideRepos.includes(r) ? "off" : ""}`} onClick={() => toggle(hideRepos, setHideRepos, r)} aria-pressed={!hideRepos.includes(r)}>{r}</button>
            ))}
          </>}
          <span style={{ flex: 1 }} />
          {(hide.length > 0 || hideRepos.length > 0) && <button className="chip" onClick={() => { setHide([]); setHideRepos([]); }}>Reset filters</button>}
          <label className="row" style={{ gap: 6, fontSize: 12.5 }}><input type="checkbox" checked={edgeLabels} onChange={(e) => setEdgeLabels(e.target.checked)} />Call counts</label>
        </div>
      </div>

      <Err msg={overview.error || focused.error} />
      <div className="gwrap">
        <div className="gcanvas">
          {!g ? <Empty>Loading…</Empty> : g.nodes.length === 0 ? <Empty>Nothing to draw yet: onboard and index a repo first.</Empty> : (
            <>
              <CyGraph elements={elements} layout={lay} onSelect={setSel} onReady={(cy) => (cyRef.current = cy)} opts={{ query, hide, hideRepos, edgeLabels }} />
              <div className="zoom">
                <button onClick={() => zoom(1.3)} aria-label="Zoom in">+</button>
                <button onClick={() => zoom(1 / 1.3)} aria-label="Zoom out">−</button>
                <button onClick={fit} aria-label="Fit to screen" title="Fit">⤢</button>
                <button onClick={exportPng} aria-label="Export PNG" title="Export PNG">↓</button>
              </div>
              <div className="gstat">{g.nodes.length} nodes · {g.edges.length} edges</div>
            </>
          )}
        </div>
        <div className="card gside">
          {!sel ? (
            <div className="hint">
              <b style={{ color: "var(--text)" }}>How to read this</b><br />
              Click a node for details. Hover a node to isolate its connections. Scroll to zoom, drag to pan.<br /><br />
              Toggle chips above to hide kinds of nodes or edges; the layout re-packs what is left. Dotted edges are ambiguous matches: verify before relying on them.
            </div>
          ) : (
            <>
              <span className="badge" style={{ color: kindColor(sel.kind), borderColor: "currentColor" }}>{(NODE_KINDS[sel.kind] || { label: sel.kind }).label}</span>
              <h4 style={{ marginTop: 8 }}>{sel.label}</h4>
              {sel.file && <div className="mono muted" style={{ overflowWrap: "anywhere" }}>{sel.file}</div>}
              {sel.size !== undefined && <div className="muted">{sel.size} symbols</div>}
              {[["Called by / used by", links.inc], ["Calls / uses", links.out]].map(([title, list]: any) => list.length > 0 && (
                <div className="nb" key={title}><b>{title} ({list.length})</b>
                  {list.slice(0, 12).map((l: any, i: number) => <a key={i} onClick={() => select(l.id)}>{l.name}{l.conf === "ambiguous" ? <span className="muted"> · ambiguous</span> : null}</a>)}
                  {list.length > 12 && <span className="muted">+{list.length - 12} more</span>}
                </div>
              ))}
              <div className="btns">
                {symbolSel && <>
                  <button className="primary" onClick={() => setFocus(sel.id)}>Focus on this</button>
                  <button onClick={() => nav("explorer", { repo, symbol: sel.id })}>Open in Explorer</button>
                  <button onClick={() => nav("impact", { repo, target: sel.id })}>Impact analysis</button>
                </>}
                {sel.kind === "table" && <button onClick={() => nav("lineage", { table: sel.label })}>Table lineage</button>}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
