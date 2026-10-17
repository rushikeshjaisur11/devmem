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

export function CyGraph({ elements, layout = "cose", onSelect, height }: { elements: Json[]; layout?: string; onSelect?: (d: Json) => void; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [theme, setTheme] = useState(0);
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
    const text = cssVar("--text"), text2 = cssVar("--text-2"), line = cssVar("--line"), accent = cssVar("--accent"),
      surface = cssVar("--surface-2"), good = cssVar("--good"), warn = cssVar("--warn");
    const ids = new Set(elements.filter((e) => !e.data.source).map((e) => e.data.id));
    const safe = elements.filter((e) => !e.data.source || (ids.has(e.data.source) && ids.has(e.data.target)));
    let cy: cytoscape.Core;
    try { cy = cytoscape({
      container: ref.current,
      elements: safe,
      layout: { name: layout, animate: false, padding: 30, nodeDimensionsIncludeLabels: true, nodeRepulsion: () => 40000, idealEdgeLength: () => 90, nestingFactor: 1.5, componentSpacing: 80, nodeOverlap: 30, numIter: 2500 } as any,
      wheelSensitivity: 0.3,
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 13, "font-weight": 500, color: text, "background-color": surface, "border-width": 1, "border-color": line, "text-valign": "center", "text-halign": "center", shape: "round-rectangle", width: "label", height: "label", padding: "9px" } as any },
        { selector: "node[kind = 'repo']", style: { "background-opacity": 0, "text-valign": "top", "font-weight": "bold", "border-color": text2, "border-width": 1.5, "border-style": "dashed", padding: "18px", width: "auto", height: "auto", shape: "rectangle" } as any },
        { selector: "node[kind = 'table']", style: { shape: "diamond", "background-color": warn, color: "#000", padding: "12px" } as any },
        { selector: "node[kind = 'job']", style: { "background-color": surface, "border-color": accent } as any },
        { selector: "node[kind = 'test']", style: { "border-color": good } as any },
        { selector: "node[?focus]", style: { "background-color": accent, color: "#fff", "font-weight": "bold" } as any },
        { selector: "edge", style: { width: 1.5, "line-color": line, "target-arrow-color": text2, "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.8 } as any },
        { selector: "edge[weight]", style: { width: "mapData(weight, 1, 20, 1.5, 4)" } as any },
        { selector: "edge[kind = 'writes']", style: { "line-color": warn, "target-arrow-color": warn } as any },
        { selector: "edge[kind = 'reads']", style: { "line-color": accent, "target-arrow-color": accent, "line-style": "dashed" } as any },
        { selector: ":selected", style: { "border-width": 3, "border-color": accent } as any },
      ],
    }); } catch (e) { console.error("graph failed", e); return; }
    cy.on("tap", "node", (ev) => onSelect && onSelect(ev.target.data()));
    return () => cy.destroy();
  }, [JSON.stringify(elements), layout, theme]); // eslint-disable-line
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
