import { useCallback, useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import { api, Json } from "./api";

export function useAsync<T = Json>(fn: () => Promise<T>, deps: any[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const run = useCallback(() => {
    setLoading(true); setError("");
    fn().then(setData).catch((e) => { setError(String(e.message || e)); setData(null); }).finally(() => setLoading(false));
  }, deps); // eslint-disable-line
  useEffect(run, [run]);
  return { data, error, loading, reload: run };
}

export function useRepos() {
  return useAsync<Json[]>(() => api.get("/api/repos"), []);
}

export function RepoSelect({ value, onChange, repos, indexedOnly = true }: { value: string; onChange: (v: string) => void; repos: Json[]; indexedOnly?: boolean }) {
  const list = repos.filter((r) => !indexedOnly || r.indexed);
  useEffect(() => { if (!value && list.length) onChange(list[0].name); }, [list.length]); // eslint-disable-line
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Repository">
      {list.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
    </select>
  );
}

export const Err = ({ msg }: { msg: string }) => (msg ? <div className="err" role="alert">{msg}</div> : null);
export const Badge = ({ kind = "", children }: { kind?: string; children: any }) => <span className={`badge ${kind}`}>{children}</span>;
export const Empty = ({ children }: { children: any }) => (
  <div className="empty"><div className="ico"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 4h16v12H5.2L4 17.2z"/><path d="M8 9h8M8 12h5"/></svg></div>{children}</div>
);

/* ---------- graph ---------- */
function cssVar(n: string) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

/** One source of truth for how each node/edge kind looks, used by both the canvas and the legends. */
export const NODE_KINDS: Record<string, { label: string; color: string }> = {
  repo: { label: "Repository", color: "--text-2" }, module: { label: "Module", color: "--text-3" },
  function: { label: "Function", color: "--accent" }, method: { label: "Method", color: "--accent-2" },
  class: { label: "Class", color: "--c-class" }, test: { label: "Test", color: "--good" },
  table: { label: "Table", color: "--warn" }, job: { label: "Job", color: "--accent" },
};
export const EDGE_KINDS: Record<string, { label: string; color: string; dashed?: boolean }> = {
  calls: { label: "calls", color: "--text-3" }, tests: { label: "tested by", color: "--good", dashed: true },
  writes: { label: "writes", color: "--warn" }, reads: { label: "reads", color: "--accent", dashed: true },
};
export const kindColor = (k: string) => `var(${(NODE_KINDS[k] || { color: "--text-3" }).color})`;

export interface GraphOpts { query?: string; hide?: string[]; hideRepos?: string[]; edgeLabels?: boolean }
const LAYOUTS: Record<string, any> = {
  cose: { name: "cose", animate: false, padding: 30, nodeDimensionsIncludeLabels: true, nodeRepulsion: () => 60000, idealEdgeLength: () => 110, nestingFactor: 1.5, componentSpacing: 90, nodeOverlap: 30, numIter: 2500 },
  breadthfirst: { name: "breadthfirst", directed: true, animate: false, padding: 30, spacingFactor: 1.25 },
  concentric: { name: "concentric", animate: false, padding: 30, minNodeSpacing: 18, spacingFactor: 1.1, concentric: (n: any) => 100 - (n.scratch("d") ?? 50), levelWidth: () => 1 },
  circle: { name: "circle", animate: false, padding: 30 },
  grid: { name: "grid", animate: false, padding: 30, avoidOverlap: true },
};
/** Hop distance from the focus node (or, with none, the busiest node) so radial rings mean "1 hop, 2 hops...". */
function markDistances(cy: cytoscape.Core) {
  const roots = cy.nodes("[?focus]").length ? cy.nodes("[?focus]") : cy.nodes().sort((a, b) => b.degree() - a.degree()).slice(0, 1);
  cy.nodes().forEach((n) => { n.scratch("d", 50); });
  cy.elements().bfs({ roots, directed: false, visit: (v, _e, _u, _i, depth) => { v.scratch("d", depth); } });
}
/** Left-to-right flow around the focus: tests and callers on the left, callees on the right, one column per hop. */
function columnPositions(cy: cytoscape.Core, eles: cytoscape.Collection) {
  const nodes = eles.nodes(), edges = eles.edges();
  const root = nodes.filter("[?focus]").length ? nodes.filter("[?focus]")[0] : nodes.sort((a, b) => b.degree() - a.degree())[0];
  const col = new Map<string, number>([[root.id(), 0]]);
  const queue: cytoscape.NodeSingular[] = [root];
  while (queue.length) {
    const n = queue.shift()!;
    edges.forEach((e) => {
      const out = e.source().id() === n.id(), inc = e.target().id() === n.id();
      if (!out && !inc) return;
      const other = out ? e.target() : e.source();
      if (!col.has(other.id())) { col.set(other.id(), (col.get(n.id()) || 0) + (out ? 1 : -1)); queue.push(other); }
    });
  }
  const groups = new Map<number, cytoscape.NodeSingular[]>();
  nodes.forEach((n) => { const c = col.get(n.id()) ?? 0; groups.set(c, [...(groups.get(c) || []), n]); });
  const keys = [...groups.keys()].sort((a, b) => a - b);
  let x = 0;
  const pos: Record<string, { x: number; y: number }> = {};
  keys.forEach((k) => {
    const list = groups.get(k)!.sort((a, b) => (a.data("kind") === "test" ? 1 : 0) - (b.data("kind") === "test" ? 1 : 0) || String(a.data("label")).localeCompare(String(b.data("label"))));
    const w = Math.max(...list.map((n) => n.width()));
    list.forEach((n, i) => { pos[n.id()] = { x: x + w / 2, y: (i - (list.length - 1) / 2) * 34 }; });
    x += w + 150;
  });
  return pos;
}
function runLayout(cy: cytoscape.Core, name: string, eles: cytoscape.Collection, animate = false) {
  if (name === "columns") {
    const pos = columnPositions(cy, eles);
    return eles.nodes().layout({ name: "preset", positions: (n: any) => pos[n.id()] || { x: 0, y: 0 }, fit: true, padding: 30, animate, animationDuration: 250 } as any).run();
  }
  return eles.layout({ ...(LAYOUTS[name] || LAYOUTS.cose), animate, animationDuration: 250, fit: true } as any).run();
}
export const LAYOUT_LABELS: Record<string, string> = { columns: "Flow (left→right)", cose: "Force", breadthfirst: "Hierarchy", concentric: "Radial", circle: "Circle", grid: "Grid" };

export function CyGraph({ elements, layout = "cose", onSelect, height, opts = {}, onReady }: { elements: Json[]; layout?: string; onSelect?: (d: Json | null) => void; height?: number; opts?: GraphOpts; onReady?: (cy: cytoscape.Core) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const hideKey = useRef("");
  const [theme, setTheme] = useState(0);
  const [built, setBuilt] = useState(0);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const f = () => setTheme((t) => t + 1);
    mq.addEventListener("change", f);
    const mo = new MutationObserver(f);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => { mq.removeEventListener("change", f); mo.disconnect(); };
  }, []);
  useEffect(() => {
    if (!ref.current) return;
    const text = cssVar("--text"), text2 = cssVar("--text-2"), line = cssVar("--line-2"), accent = cssVar("--accent"), surface = cssVar("--surface");
    const ids = new Set(elements.filter((e) => !e.data.source).map((e) => e.data.id));
    const safe = elements.filter((e) => !e.data.source || (ids.has(e.data.source) && ids.has(e.data.target)));
    const kindStyles = Object.entries(NODE_KINDS).map(([k, v]) => ({
      selector: `node[kind = '${k}']`,
      style: { "border-color": cssVar(v.color), "background-color": cssVar(v.color), "background-opacity": 0.14, ...(k === "table" ? { shape: "diamond", padding: "14px" } : {}) } as any,
    }));
    const edgeStyles = Object.entries(EDGE_KINDS).map(([k, v]) => ({
      selector: `edge[kind = '${k}']`,
      style: { "line-color": cssVar(v.color), "target-arrow-color": cssVar(v.color), "line-style": v.dashed ? "dashed" : "solid" } as any,
    }));
    let cy: cytoscape.Core;
    try { cy = cytoscape({
      container: ref.current,
      elements: safe,
      wheelSensitivity: 0.25, minZoom: 0.15, maxZoom: 3,
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 13, "font-weight": 500, color: text, "background-color": text2, "background-opacity": 0.1, "border-width": 1.5, "border-color": line, "text-valign": "center", "text-halign": "center", shape: "round-rectangle", width: "label", height: "label", padding: "10px", "transition-property": "opacity, border-width", "transition-duration": 120 } as any },
        ...kindStyles,
        { selector: "node[kind = 'repo']", style: { "background-opacity": 0.03, "text-valign": "top", "text-margin-y": -4, "font-weight": 700, "font-size": 14, "border-style": "dashed", padding: "22px", width: "auto", height: "auto", shape: "round-rectangle" } as any },
        { selector: "node[kind = 'module']", style: { "border-width": 1.5 } as any },
        { selector: "node[?focus]", style: { "background-color": accent, "background-opacity": 1, color: "#fff", "font-weight": 700, "border-width": 3, "border-color": accent } as any },
        { selector: "edge", style: { width: 1.6, "line-color": line, "target-arrow-color": line, "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.9, opacity: 0.9 } as any },
        ...edgeStyles,
        { selector: "edge[conf = 'ambiguous']", style: { "line-style": "dotted", opacity: 0.55 } as any },
        { selector: "edge[weight]", style: { width: "mapData(weight, 1, 20, 1.6, 5)" } as any },
        { selector: "edge.lbl", style: { label: "data(weight)", "font-size": 11, color: text2, "text-background-color": surface, "text-background-opacity": 0.9, "text-background-padding": "2px", "text-rotation": "autorotate" } as any },
        { selector: ".hidden", style: { display: "none" } as any },
        { selector: ".faded", style: { opacity: 0.12 } as any },
        { selector: ".dim", style: { opacity: 0.2 } as any },
        { selector: ".match", style: { "border-width": 3.5, "border-color": accent, "background-opacity": 0.35 } as any },
        { selector: ":selected", style: { "border-width": 3.5, "border-color": accent, "overlay-opacity": 0 } as any },
        { selector: "edge:selected", style: { width: 3, "line-color": accent, "target-arrow-color": accent } as any },
      ],
    }); } catch (e) { console.error("graph failed", e); return; }
    cy.on("tap", (ev) => { if (ev.target === cy) onSelect && onSelect(null); });
    cy.on("tap", "node", (ev) => onSelect && onSelect(ev.target.data()));
    cy.on("mouseover", "node", (ev) => { if (cy.scratch("q")) return; const nb = ev.target.closedNeighborhood(); cy.elements().not(nb).addClass("dim"); });
    cy.on("mouseout", "node", () => cy.elements().removeClass("dim"));
    markDistances(cy);
    runLayout(cy, layout, cy.elements());
    cyRef.current = cy; hideKey.current = "";
    onReady && onReady(cy);
    setBuilt((n) => n + 1);
    return () => { cy.destroy(); cyRef.current = null; };
  }, [JSON.stringify(elements), layout, theme]); // eslint-disable-line

  // filters, search highlight, edge labels: applied in place, no refetch
  const hide = opts.hide || [], hideRepos = opts.hideRepos || [], query = (opts.query || "").trim().toLowerCase();
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const H = new Set(hide), HR = new Set(hideRepos);
    cy.batch(() => {
      cy.elements().removeClass("hidden faded match");
      cy.nodes().forEach((n) => {
        const d = n.data(), repo = d.kind === "repo" ? d.label : typeof d.parent === "string" ? d.parent.replace(/^repo:/, "") : "";
        if (H.has(d.kind) || (repo && HR.has(repo))) n.addClass("hidden");
      });
      cy.edges().forEach((e) => { if (H.has("edge:" + e.data("kind"))) e.addClass("hidden"); });
      cy.edges().removeClass("lbl"); if (opts.edgeLabels) cy.edges("[weight > 1]").addClass("lbl");
      cy.scratch("q", query);
      if (query) {
        const vis = cy.nodes().not(".hidden").filter((n) => n.data("kind") !== "repo");
        const hit = vis.filter((n) => `${n.data("label")} ${n.data("file") || ""}`.toLowerCase().includes(query));
        hit.addClass("match");
        vis.not(hit).addClass("faded");
        cy.edges().forEach((e) => { if (!(e.source().hasClass("match") && e.target().hasClass("match"))) e.addClass("faded"); });
      }
    });
    const key = hide.join(",") + "|" + hideRepos.join(",");
    if (key !== hideKey.current) { // filter set changed: re-pack what is left
      const first = hideKey.current === "";
      hideKey.current = key;
      if (!first) runLayout(cy, layout, cy.elements(":visible"), true);
    }
  }, [built, hide.join(","), hideRepos.join(","), query, opts.edgeLabels]); // eslint-disable-line
  return <div ref={ref} className="graph" style={height ? { height } : undefined} role="img" aria-label="Dependency graph" />;
}

/* ---------- single-series line chart (SVG) ---------- */
export function LineChart({ points, label, width = 640, height = 200 }: { points: { x: string; y: number }[]; label: string; width?: number; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return <Empty>Not enough runs yet for a trend: run it again after changes to see {label.toLowerCase()} over time.</Empty>;
  const pad = { l: 38, r: 16, t: 12, b: 24 };
  const ys = points.map((p) => p.y);
  const max = Math.max(...ys, 1), min = Math.min(...ys, 0);
  const sx = (i: number) => pad.l + (i * (width - pad.l - pad.r)) / (points.length - 1);
  const sy = (v: number) => pad.t + (1 - (v - min) / (max - min || 1)) * (height - pad.t - pad.b);
  const ticks = [min, Math.round((min + max) / 2), max];
  const path = points.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={`${label}: ${points.map((p) => `${p.x} ${p.y}`).join(", ")}`}
      onMouseLeave={() => setHover(null)}
      onMouseMove={(e) => {
        const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
        const x = ((e.clientX - r.left) / r.width) * width;
        setHover(Math.max(0, Math.min(points.length - 1, Math.round(((x - pad.l) / (width - pad.l - pad.r)) * (points.length - 1)))));
      }}>
      {ticks.map((t, i) => (
        <g key={i}><line className="grid" x1={pad.l} x2={width - pad.r} y1={sy(t)} y2={sy(t)} /><text x={pad.l - 6} y={sy(t) + 4} textAnchor="end">{t}</text></g>
      ))}
      <path className="line" d={path} />
      <text x={pad.l} y={height - 6}>{points[0].x}</text>
      <text x={width - pad.r} y={height - 6} textAnchor="end">{points[points.length - 1].x}</text>
      <circle className="dot" cx={sx(points.length - 1)} cy={sy(points[points.length - 1].y)} r={4} />
      {hover !== null && (
        <g>
          <line className="grid" x1={sx(hover)} x2={sx(hover)} y1={pad.t} y2={height - pad.b} />
          <circle className="dot" cx={sx(hover)} cy={sy(points[hover].y)} r={4.5} />
          <text x={Math.min(sx(hover) + 8, width - 90)} y={pad.t + 10} style={{ fill: "var(--text)", fontWeight: 600 }}>{points[hover].y} · {points[hover].x}</text>
        </g>
      )}
    </svg>
  );
}

export function Bars({ rows }: { rows: { label: string; value: number; sub?: string }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <table>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label}>
            <td style={{ width: 110 }}>{r.label}</td>
            <td><div className="bar" style={{ width: `${(r.value / max) * 100}%` }} title={`${r.label}: ${r.value}`} /></td>
            <td className="num" style={{ width: 90 }}>{r.value}{r.sub ? <span className="muted"> {r.sub}</span> : null}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

import { Component } from "react";
export class ErrorBoundary extends Component<{ children: any }, { error: string }> {
  state = { error: "" };
  static getDerivedStateFromError(e: Error) { return { error: String(e.message || e) }; }
  render() {
    return this.state.error ? <div className="card"><div className="err" role="alert">This screen hit an error: {this.state.error}</div><button onClick={() => this.setState({ error: "" })}>Try again</button></div> : this.props.children;
  }
}
