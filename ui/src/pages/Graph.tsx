import { useEffect, useMemo, useRef, useState } from "react";
import type cytoscape from "cytoscape";
import { api, q } from "../api";
import { Empty, Err, RepoSelect, SymbolSearch, useAsync, useRepos } from "../components";
import { CyGraph, EDGE_KINDS, NODE_KINDS, kindColor } from "../graph";
import { DEFAULT_SETTINGS, ForceMap, GROUP_HUES, KIND_HUE, MapLink, MapNode, MapSettings, folderOf } from "../forcemap";
import { nav } from "../route";

const LINK_LABEL: Record<string, string> = { calls: "Calls", tests: "Tests", reads: "Table reads", writes: "Table writes", imports: "Imports from another repo" };
const KIND_LABEL: Record<string, string> = { file: "File", function: "Function", method: "Method", class: "Class", test: "Test", table: "Table" };
const endId = (e: string | MapNode) => (typeof e === "string" ? e : e.id);
const n = (x: number) => x.toLocaleString();

function load<T>(k: string, d: T): T { try { const v = localStorage.getItem(k); return v ? { ...d, ...JSON.parse(v) } : d; } catch { return d; } }
function save(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } }

interface Filters { query: string; tests: boolean; tables: boolean; orphans: boolean; hideRepos: string[]; hideLinks: string[] }
const DEFAULT_FILTERS: Filters = { query: "", tests: true, tables: true, orphans: false, hideRepos: [], hideLinks: [] };
interface View { name: string; level: "file" | "symbol"; f: Filters; s: MapSettings }

function Section({ title, open, children }: { title: string; open?: boolean; children: any }) {
  return <details className="gp-sec" open={open}><summary>{title}</summary><div className="gp-body">{children}</div></details>;
}
function Slider({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void }) {
  return <label className="gp-slider"><span>{label}</span><input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(+e.target.value)} /></label>;
}

/** The whole workspace as one Obsidian-style map. */
function WorkspaceMap({ onFocus }: { onFocus: (repo: string, sid: string) => void }) {
  const [level, setLevel] = useState<"file" | "symbol">(() => load("devmem-map-level", { v: "file" as "file" | "symbol" }).v);
  const [f, setF] = useState<Filters>(() => ({ ...load("devmem-map-filters", DEFAULT_FILTERS), query: "" }));
  const [s, setS] = useState<MapSettings>(() => load("devmem-map-settings", DEFAULT_SETTINGS));
  const [sel, setSel] = useState<MapNode | null>(null);
  const [local, setLocal] = useState(0);   // 0 = whole map, 1-3 = hops around the selected node
  const [panel, setPanel] = useState(() => load("devmem-map-panel", { v: false }).v);
  const [views, setViews] = useState<View[]>(() => load("devmem-map-views", { v: [] as View[] }).v);
  const [viewName, setViewName] = useState("");
  const fgRef = useRef<any>(null);
  useEffect(() => save("devmem-map-views", { v: views }), [views]);
  const saveView = () => { const name = viewName.trim(); if (!name) return; setViews([...views.filter((v) => v.name !== name), { name, level, f, s }]); setViewName(""); };
  const applyView = (v: View) => { setLevel(v.level); setF({ ...DEFAULT_FILTERS, ...v.f }); setS({ ...DEFAULT_SETTINGS, ...v.s }); };
  const data = useAsync(() => api.get(`/api/graph/all?${q({ level })}`), [level]);
  useEffect(() => save("devmem-map-level", { v: level }), [level]);
  useEffect(() => { const { query, ...rest } = f; save("devmem-map-filters", rest); }, [f]);
  useEffect(() => save("devmem-map-settings", s), [s]);
  useEffect(() => save("devmem-map-panel", { v: panel }), [panel]);
  useEffect(() => { setSel(null); setLocal(0); }, [level]);

  const raw = data.data as { nodes: MapNode[]; links: MapLink[] } | null;
  const repos = useMemo(() => [...new Set((raw?.nodes || []).map((x) => x.repo).filter(Boolean))].sort(), [raw]);
  const kinds = useMemo(() => [...new Set((raw?.nodes || []).map((x) => x.kind))], [raw]);
  // folders by size: the four biggest get a hue, the rest stay grey (four hues at most on the screen)
  const folders = useMemo(() => {
    const c = new Map<string, number>();
    (raw?.nodes || []).forEach((x) => { if (x.kind !== "table") c.set(folderOf(x), (c.get(folderOf(x)) || 0) + 1); });
    return [...c.entries()].sort((a, b) => b[1] - a[1]);
  }, [raw]);
  const linkKinds = useMemo(() => [...new Set((raw?.links || []).map((l) => l.kind))], [raw]);

  const view = useMemo(() => {
    if (!raw) return { nodes: [] as MapNode[], links: [] as MapLink[] };
    const qy = f.query.trim().toLowerCase(), HR = new Set(f.hideRepos), HL = new Set(f.hideLinks);
    let ns = raw.nodes.filter((x) => (f.tests || x.kind !== "test") && (f.tables || x.kind !== "table") && !HR.has(x.repo)
      && (!qy || `${x.label} ${x.file || ""}`.toLowerCase().includes(qy)));
    let ids = new Set(ns.map((x) => x.id));
    let ls = raw.links.filter((l) => !HL.has(l.kind) && ids.has(endId(l.source)) && ids.has(endId(l.target)));
    if (local && sel && ids.has(sel.id)) {   // local graph: only what is within N hops of the selection
      const keep = new Set([sel.id]); let front = [sel.id];
      for (let d = 0; d < local; d++) {
        const nx: string[] = [];
        ls.forEach((l) => { const a = endId(l.source), b = endId(l.target);
          if (front.includes(a) && !keep.has(b)) { keep.add(b); nx.push(b); }
          if (front.includes(b) && !keep.has(a)) { keep.add(a); nx.push(a); } });
        front = nx;
      }
      ns = ns.filter((x) => keep.has(x.id)); ids = keep;
      ls = ls.filter((l) => keep.has(endId(l.source)) && keep.has(endId(l.target)));
    }
    if (!f.orphans) {
      const linked = new Set<string>(); ls.forEach((l) => { linked.add(endId(l.source)); linked.add(endId(l.target)); });
      ns = ns.filter((x) => linked.has(x.id) || x.id === sel?.id);
    }
    return { nodes: ns, links: ls };
  }, [raw, f, local, sel?.id]); // eslint-disable-line

  const nb = useMemo(() => {
    if (!sel || !raw) return { out: [] as [MapNode, string][], inc: [] as [MapNode, string][] };
    const byId = new Map(raw.nodes.map((x) => [x.id, x]));
    const out: [MapNode, string][] = [], inc: [MapNode, string][] = [];
    raw.links.forEach((l) => { const a = endId(l.source), b = endId(l.target);
      if (a === sel.id && byId.has(b)) out.push([byId.get(b)!, l.kind]);
      if (b === sel.id && byId.has(a)) inc.push([byId.get(a)!, l.kind]); });
    return { out, inc };
  }, [sel, raw]);

  const groups = s.colorBy === "repo" ? repos : s.colorBy === "folder" ? folders.slice(0, GROUP_HUES.length).map(([k]) => k) : kinds;
  const hue = (g: string) => { const i = groups.indexOf(g); return s.colorBy === "kind" ? `var(${KIND_HUE[g] || "--text-3"})` : i >= 0 && i < GROUP_HUES.length ? `var(${GROUP_HUES[i]})` : "var(--text-3)"; };
  const toggle = (k: "hideRepos" | "hideLinks", v: string) => setF((x) => ({ ...x, [k]: x[k].includes(v) ? x[k].filter((y) => y !== v) : [...x[k], v] }));
  const pick = (x: MapNode) => { setSel(x); const g = fgRef.current; if (g && x.x !== undefined) { g.centerAt(x.x, x.y, 400); g.zoom(Math.max(g.zoom(), 2.5), 400); } };
  const unit = level === "file" ? "files" : "symbols";

  return (
    <>
      <div className="gtools">
        <div className="seg" role="group" aria-label="Show">
          <button className={level === "file" ? "on" : ""} aria-pressed={level === "file"} onClick={() => setLevel("file")}>Files</button>
          <button className={level === "symbol" ? "on" : ""} aria-pressed={level === "symbol"} onClick={() => setLevel("symbol")}>Functions and classes</button>
        </div>
        <div className="search grow">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input placeholder={`Filter ${unit} by name or path`} value={f.query} onChange={(e) => setF({ ...f, query: e.target.value })} aria-label={`Filter ${unit}`} />
        </div>
        <span className="muted num">{n(view.nodes.length)} {unit} · {n(view.links.length)} links</span>
        {views.length > 0 && <select aria-label="Saved views" value="" onChange={(e) => { const v = views.find((x) => x.name === e.target.value); if (v) applyView(v); }}>
          <option value="" disabled>Saved views</option>
          {views.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
        </select>}
        <button onClick={() => fgRef.current?.zoomToFit(400, 40)}>Fit to screen</button>
        <button className={panel ? "on" : ""} aria-pressed={panel} onClick={() => setPanel(!panel)}>Graph settings</button>
      </div>
      <Err msg={data.error} />
      <div className={`gwrap ${sel ? "" : "solo"}`}>
        <div className="gcanvas">
          {raw && raw.nodes.length > 0 && !sel && <div className="gstat">Bigger dots have more links. Hover to see connections, zoom in to read names, click to open one.</div>}
          {!raw ? <div className="fmap fmap-empty"><Empty>Laying out the map…</Empty></div> : raw.nodes.length === 0 ? <div className="fmap fmap-empty"><Empty>Nothing to draw yet. <a href="#/repos">Add and index a repo</a> first.</Empty></div> : (
            <ForceMap nodes={view.nodes} links={view.links} settings={s} groups={groups} selected={sel?.id || null}
              onSelect={(x) => { if (x) pick(x); else { setSel(null); setLocal(0); } }} onReady={(g) => (fgRef.current = g)} />
          )}
          {panel && raw && (
            <div className="gpanel" aria-label="Graph settings">
              <Section title="Filters" open>
                <label className="gp-check"><input type="checkbox" checked={f.tests} onChange={(e) => setF({ ...f, tests: e.target.checked })} />Tests</label>
                <label className="gp-check"><input type="checkbox" checked={f.tables} onChange={(e) => setF({ ...f, tables: e.target.checked })} />Database tables</label>
                <label className="gp-check"><input type="checkbox" checked={f.orphans} onChange={(e) => setF({ ...f, orphans: e.target.checked })} />{level === "file" ? "Files" : "Symbols"} with no links</label>
                <div className="gp-label">Links</div>
                {linkKinds.map((k) => <label key={k} className="gp-check"><input type="checkbox" checked={!f.hideLinks.includes(k)} onChange={() => toggle("hideLinks", k)} />{LINK_LABEL[k] || k}</label>)}
              </Section>
              <Section title="Groups" open>
                <div className="seg sm" role="group" aria-label="Colour by">
                  <button className={s.colorBy === "repo" ? "on" : ""} onClick={() => setS({ ...s, colorBy: "repo" })}>By repo</button>
                  <button className={s.colorBy === "folder" ? "on" : ""} onClick={() => setS({ ...s, colorBy: "folder" })}>By folder</button>
                  <button className={s.colorBy === "kind" ? "on" : ""} onClick={() => setS({ ...s, colorBy: "kind" })}>By kind</button>
                </div>
                {s.colorBy === "folder" ? <>
                  {folders.slice(0, GROUP_HUES.length).map(([k, c]) => <div key={k} className="gp-check"><i style={{ background: hue(k) }} /><span className="gp-name" title={k}>{k}</span><span className="muted num">{n(c)}</span></div>)}
                  {folders.length > GROUP_HUES.length && <div className="gp-check"><i style={{ background: "var(--text-3)" }} /><span className="gp-name">{folders.length - GROUP_HUES.length} smaller folders</span></div>}
                </> : s.colorBy === "repo" ? repos.map((r) => (
                  <label key={r} className="gp-check"><input type="checkbox" checked={!f.hideRepos.includes(r)} onChange={() => toggle("hideRepos", r)} /><i style={{ background: hue(r) }} />{r}</label>
                )) : kinds.map((k) => <div key={k} className="gp-check"><i style={{ background: hue(k) }} />{KIND_LABEL[k] || k}</div>)}
              </Section>
              <Section title="Display">
                <label className="gp-check"><input type="checkbox" checked={s.arrows} onChange={(e) => setS({ ...s, arrows: e.target.checked })} />Arrows</label>
                <Slider label="Text fade threshold" value={s.textFade} min={0.2} max={4} step={0.1} onChange={(v) => setS({ ...s, textFade: v })} />
                <Slider label="Node size" value={s.nodeSize} min={0.4} max={3} step={0.1} onChange={(v) => setS({ ...s, nodeSize: v })} />
                <Slider label="Link thickness" value={s.linkWidth} min={0.2} max={3} step={0.1} onChange={(v) => setS({ ...s, linkWidth: v })} />
              </Section>
              <Section title="Forces">
                <Slider label="Center force" value={s.center} min={0} max={0.3} step={0.01} onChange={(v) => setS({ ...s, center: v })} />
                <Slider label="Repel force" value={s.repel} min={5} max={300} step={5} onChange={(v) => setS({ ...s, repel: v })} />
                <Slider label="Link force" value={s.linkForce} min={0.05} max={1} step={0.05} onChange={(v) => setS({ ...s, linkForce: v })} />
                <Slider label="Link distance" value={s.linkDistance} min={10} max={200} step={5} onChange={(v) => setS({ ...s, linkDistance: v })} />
              </Section>
              <Section title="Saved views" open>
                <form className="gp-save" onSubmit={(e) => { e.preventDefault(); saveView(); }}>
                  <input value={viewName} onChange={(e) => setViewName(e.target.value)} placeholder="Name this view" aria-label="View name" />
                  <button type="submit" disabled={!viewName.trim()}>Save</button>
                </form>
                {views.length === 0 ? <span className="muted">Saves the level, filters, groups and forces you have set now.</span> : views.map((v) => (
                  <div key={v.name} className="gp-view">
                    <button className="quiet" onClick={() => applyView(v)} title={`Show ${v.name}`}>{v.name}</button>
                    <button className="quiet" aria-label={`Delete view ${v.name}`} title="Delete view" onClick={() => setViews(views.filter((x) => x.name !== v.name))}>
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
                    </button>
                  </div>
                ))}
              </Section>
              <button className="quiet gp-reset" onClick={() => { setS(DEFAULT_SETTINGS); setF(DEFAULT_FILTERS); }}>Reset to defaults</button>
            </div>
          )}
        </div>
        {sel && <div className="card gside">
          {(
            <>
              <span className="badge">{KIND_LABEL[sel.kind] || sel.kind}{sel.repo ? ` in ${sel.repo}` : ""}</span>
              <h4 style={{ marginTop: 8 }}>{sel.label}</h4>
              {sel.file && sel.file !== sel.label && <div className="mono muted" style={{ overflowWrap: "anywhere", fontSize: 12 }}>{sel.file}</div>}
              <div className="gp-local">
                <span>Neighbourhood</span>
                <div className="seg sm" role="group" aria-label="Show around this">
                  {[0, 1, 2, 3].map((d) => <button key={d} className={local === d ? "on" : ""} aria-pressed={local === d} onClick={() => setLocal(d)}>{d === 0 ? "All" : `${d} hop${d > 1 ? "s" : ""}`}</button>)}
                </div>
              </div>
              {([["Used by", nb.inc], ["Uses", nb.out]] as [string, [MapNode, string][]][]).map(([title, list]) => list.length > 0 && (
                <div className="nb" key={title}><b>{title} ({list.length})</b>
                  {list.slice(0, 14).map(([x, k], i) => <a key={i} onClick={() => pick(x)} title={x.file}>{x.label}<span className="muted"> · {(LINK_LABEL[k] || k).toLowerCase()}</span></a>)}
                  {list.length > 14 && <span className="muted">and {list.length - 14} more</span>}
                </div>
              ))}
              {nb.inc.length + nb.out.length === 0 && <p className="muted">No links found for this {sel.kind === "table" ? "table" : "item"}.</p>}
              <div className="btns">
                {sel.sid && <button className="primary" onClick={() => onFocus(sel.repo, sel.sid!)}>Show call flow</button>}
                {sel.sid && <button onClick={() => nav("impact", { repo: sel.repo, target: sel.sid })}>What breaks if this changes</button>}
                {sel.kind === "table" && <button onClick={() => nav("lineage", { table: sel.label })}>Table lineage</button>}
              </div>
            </>
          )}
        </div>}
      </div>
    </>
  );
}

export default function Graph({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const [focus, setFocus] = useState(params.focus || "");
  const [depth, setDepth] = useState(2);
  const [sel, setSel] = useState<any>(null);
  const [find, setFind] = useState("");
  const cyRef = useRef<cytoscape.Core | null>(null);
  const focused = useAsync(() => (focus && repo ? api.get(`/api/repos/${repo}/graph?${q({ focus, depth })}`) : Promise.resolve(null)), [focus, repo, depth]);
  const g = focused.data;
  useEffect(() => setSel(null), [focus, repo]);
  const elements = useMemo(() => (g ? [...g.nodes, ...g.edges] : []), [g]);
  const label = useMemo(() => Object.fromEntries((g?.nodes || []).map((x: any) => [x.data.id, x.data.label])), [g]);
  const links = useMemo(() => {
    if (!sel || !g) return { out: [], inc: [] };
    const m = (e: any, end: string) => ({ id: e.data[end], name: label[e.data[end]] || e.data[end], kind: e.data.kind, conf: e.data.conf });
    return { out: g.edges.filter((e: any) => e.data.source === sel.id).map((e: any) => m(e, "target")), inc: g.edges.filter((e: any) => e.data.target === sel.id).map((e: any) => m(e, "source")) };
  }, [sel, g, label]);
  const select = (id: string) => { const cy = cyRef.current; const x = cy?.getElementById(id); if (x && x.length) { cy!.elements().unselect(); x.select(); setSel(x.data()); cy!.animate({ center: { eles: x }, duration: 200 }); } };

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{focus ? `Call flow of ${focus.split("#")[1] || focus}` : "Code map"}</h1>
        {focus && <button onClick={() => { setFocus(""); nav("graph"); }}>Back to the whole map</button>}
      </div>
      <p className="sub">{focus ? "Callers and tests on the left, what it calls on the right, one column per hop." : "Every file or function in your repos and how they connect: calls, tests and the tables they share."}</p>
      {!focus ? <WorkspaceMap onFocus={(r, sid) => { setRepo(r); setFocus(sid); }} /> : (
        <>
          <div className="gtools">
            {repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}
            <SymbolSearch repo={repo} value={find} onChange={setFind} onPick={(id) => { if (id.includes("#")) { setFocus(id); setFind(""); } }} placeholder="Find a function: describe it or type its name" />
            <label className="row" style={{ gap: 6 }}>Hops <select value={depth} onChange={(e) => setDepth(+e.target.value)}>{[1, 2, 3].map((d) => <option key={d}>{d}</option>)}</select></label>
          </div>
          <Err msg={focused.error} />
          <div className="gwrap">
            <div className="gcanvas">
              {!g ? <Empty>Loading…</Empty> : <CyGraph elements={elements} layout="columns" onSelect={setSel} onReady={(cy) => (cyRef.current = cy)} />}
            </div>
            <div className="card gside">
              {!sel ? <div className="hint">Click a box for details. Dotted lines are ambiguous matches: check them in the code.</div> : (
                <>
                  <span className="badge" style={{ color: kindColor(sel.kind) }}>{(NODE_KINDS[sel.kind] || { label: sel.kind }).label}</span>
                  <h4 style={{ marginTop: 8 }}>{sel.label}</h4>
                  {sel.file && <div className="mono muted" style={{ overflowWrap: "anywhere", fontSize: 12 }}>{sel.file}</div>}
                  {[["Called by", links.inc], ["Calls", links.out]].map(([title, list]: any) => list.length > 0 && (
                    <div className="nb" key={title}><b>{title} ({list.length})</b>
                      {list.slice(0, 12).map((l: any, i: number) => <a key={i} onClick={() => select(l.id)}>{l.name}<span className="muted"> · {(EDGE_KINDS[l.kind] || { label: l.kind }).label}{l.conf === "ambiguous" ? ", ambiguous" : ""}</span></a>)}
                    </div>
                  ))}
                  {String(sel.id).includes("#") && <div className="btns">
                    <button className="primary" onClick={() => setFocus(sel.id)}>Center on this</button>
                    <button onClick={() => nav("impact", { repo, target: sel.id })}>What breaks if this changes</button>
                  </div>}
                </>
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
