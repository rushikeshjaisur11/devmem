import { useCallback, useEffect, useState } from "react";
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

/* ---------- plain-language explanations for devmem's terms ---------- */
export const GLOSSARY: Record<string, string> = {
  hop1: "Functions that call this one directly. Each must still work after your change.",
  hop2: "Functions that call those callers. Only check that what they rely on (arguments, return shape) is unchanged.",
  structural: "Link found by resolving imports and names. Usually right.",
  ambiguous: "Several functions share this name and devmem could not tell which one is meant. Check it in the code.",
  precise: "Link confirmed by a type-aware indexer (SCIP).",
  index: "devmem's map of the repo: functions, who calls whom, tests and tables. Rebuilt when files change.",
  stale: "The repo has commits newer than its map, so agents may see old code until it is re-indexed.",
  publish: "Writes the map agents read (.devmem/ and a short AGENTS.md block) into the repo. Nothing else is changed.",
  knowledge: "Short Markdown files agents read instead of whole source files.",
  session: "What every agent conversation in this repo starts with: the devmem block in AGENTS.md.",
  tokens: "The units an AI model reads, about 4 characters each. Fewer tokens means cheaper and faster agents.",
  mcp: "Model Context Protocol: lets an agent call devmem as tools instead of running shell commands.",
  hub: "One git repo that collects every repo's catalog, so agents can reuse code across repos.",
  lineage: "Which jobs write and read each table, and how columns flow between them.",
  gap: "Code that many places call but no test reaches. A bug there spreads far and nothing catches it.",
};
export const Term = ({ k, children }: { k: string; children: any }) => (
  <span className="term" tabIndex={0} data-tip={GLOSSARY[k]} aria-label={`${typeof children === "string" ? children : ""}: ${GLOSSARY[k]}`}>{children}</span>
);

/** Same risk rule as `devmem review`: many callers, or callers but no test, is high. */
export const riskOf = (hop1: number, tests: number, capped: boolean) => capped || hop1 >= 5 || (hop1 > 0 && tests === 0) ? "high" : tests === 0 || hop1 > 0 ? "medium" : "low";

export function Copy({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return <button className="quiet" onClick={async () => { try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); } catch { /* clipboard blocked: text stays selectable */ } }}>{done ? "Copied" : label}</button>;
}

/* ---------- open in editor ---------- */
export const EDITORS: Record<string, [string, string]> = { vscode: ["VS Code", "vscode://file/"], cursor: ["Cursor", "cursor://file/"], none: ["Off", ""] };
export const getEditor = () => { try { return localStorage.getItem("devmem-editor") || "vscode"; } catch { return "vscode"; } };
/** Deep link that opens repo/file[:line] in the chosen editor; undefined when off or the repo path is unknown. */
export function editorHref(repos: Json[] | null, repo: string, file: string, line?: number) {
  const scheme = (EDITORS[getEditor()] || EDITORS.vscode)[1], root = repos?.find((r) => r.name === repo)?.path;
  if (!scheme || !root || !file) return undefined;
  return scheme + encodeURI(`${root}/${file}`.replace(/\\/g, "/")) + (line ? `:${line}` : "");
}
export const EditorLink = ({ href }: { href?: string }) => href ? (
  <a className="edit-link" href={href} title={`Open in ${EDITORS[getEditor()][0]}`} aria-label={`Open in ${EDITORS[getEditor()][0]}`} onClick={(e) => e.stopPropagation()}>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg>
  </a>) : null;

export const Err = ({ msg }: { msg: string }) => (msg ? <div className="err" role="alert">{msg}</div> : null);
export const Badge = ({ kind = "", children }: { kind?: string; children: any }) => <span className={`badge ${kind}`}>{children}</span>;
export const Empty = ({ children }: { children: any }) => (
  <div className="empty"><div className="ico"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 4h16v12H5.2L4 17.2z"/><path d="M8 9h8M8 12h5"/></svg></div>{children}</div>
);

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
