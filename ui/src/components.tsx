import { createContext, useCallback, useContext, useEffect, useState } from "react";
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

/* ---------- the current repo: chosen once in the top bar, used by every page ---------- */
export interface RepoCtx { repo: string; setRepo: (v: string) => void; repos: Json[] | null }
export const RepoContext = createContext<RepoCtx>({ repo: "", setRepo: () => {}, repos: null });
/** The repo picked in the top bar. Pages read it here instead of each keeping (and placing) its own selector. */
export const useRepo = () => useContext(RepoContext);

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

/* ---------- find code without knowing its name ---------- */
/** "path/to/file.ts#Class.method" -> { name: "Class.method", file: "path/to/file.ts" }, for showing ids to people. */
export const splitId = (id: string) => ({ name: id.includes("#") ? id.split("#")[1] : id, file: id.includes("#") ? id.split("#")[0] : "" });

/** Type what the code does in plain words ("save settings", "retry upload") or part of a name; pick from suggestions that
 *  show each function's description. Enter without picking passes the text as typed (tables, columns, exact ids). */
export function SymbolSearch({ repo, value, onChange, onPick, placeholder = "Describe it in plain words, e.g. save settings, or type a name" }:
  { repo: string; value: string; onChange: (v: string) => void; onPick: (id: string) => void; placeholder?: string }) {
  const [hits, setHits] = useState<Json[]>([]);
  const [open, setOpen] = useState(false);
  const [i, setI] = useState(0);
  useEffect(() => {
    if (!repo || value.trim().length < 2 || value.includes("#")) { setHits([]); return; }
    const t = setTimeout(() => api.get(`/api/repos/${repo}/search?q=${encodeURIComponent(value)}&limit=8`).then(setHits).catch(() => setHits([])), 180);
    return () => clearTimeout(t);
  }, [repo, value]);
  const pick = (id: string) => { onPick(id); setOpen(false); };
  const show = open && hits.length > 0;
  return (
    <div className="search sym-search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
      <input value={value} placeholder={placeholder} role="combobox" aria-expanded={show} aria-autocomplete="list" aria-label={placeholder}
        onChange={(e) => { onChange(e.target.value); setOpen(true); setI(0); }} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setI(Math.min(i + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setI(Math.max(i - 1, 0)); }
          else if (e.key === "Escape") setOpen(false);
          else if (e.key === "Enter" && value.trim()) { e.preventDefault(); pick(show && hits[i] ? hits[i].id : value.trim()); }
        }} />
      {show && <div className="hits" role="listbox">
        {hits.map((h, n) => (
          <div key={h.id} role="option" aria-selected={n === i} className={n === i ? "on" : ""} onMouseDown={() => pick(h.id)} onMouseEnter={() => setI(n)}>
            <span><b>{h.qualname}</b>{h.doc && <small>{h.doc}</small>}</span>
            <span className="muted mono">{h.file}:{h.line}</span>
          </div>
        ))}
      </div>}
    </div>
  );
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

/* ---------- icons, menus, deployment config ---------- */
const PATHS: Record<string, string> = {
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 20h16"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.3L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.3L20 16"/><path d="M20 20v-4h-4"/>',
  send: '<path d="M4 12 20 4l-6 16-3-7z"/>',
  more: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-12M9 7V4h6v3"/>',
  shield: '<path d="M12 3 4 6v6c0 4.5 3.2 7.8 8 9 4.8-1.2 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  git: '<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="9" r="2.2"/><path d="M6 8.2v7.6M18 11.2c0 3-3 3.8-6 3.8H8.2"/>',
  plug: '<path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0zM12 16v5"/>',
  key: '<circle cx="8" cy="14" r="4"/><path d="m11 11 9-9M16 6l3 3"/>',
  code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
  blank: '',
  book: '<path d="M4 5a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2z"/><path d="M4 20a2 2 0 0 0 2 2h13v-4"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  out: '<path d="M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5M15 8l4 4-4 4M19 12H9"/>',
};
export const Icon = ({ name }: { name: string }) => (
  <svg className="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: PATHS[name] || "" }} />
);

export interface MenuItem { label: string; icon?: string; danger?: boolean; disabled?: boolean; hint?: string; onClick?: () => void; href?: string }
/** A "more actions" button: keeps a row to one or two visible buttons and puts the rest behind it. */
export function Menu({ items, label = "More actions", disabled }: { items: MenuItem[]; label?: string; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("click", close); window.addEventListener("keydown", key);
    return () => { window.removeEventListener("click", close); window.removeEventListener("keydown", key); };
  }, [open]);
  return (
    <span className="menu-wrap" onClick={(e) => e.stopPropagation()}>
      <button className="icon-btn bordered" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} disabled={disabled} onClick={() => setOpen((o) => !o)}><Icon name="more" /></button>
      {open && (
        <div className="menu" role="menu">
          {items.map((it) => it.href
            ? <a key={it.label} role="menuitem" href={it.href} onClick={() => setOpen(false)}>{it.icon && <Icon name={it.icon} />}<span>{it.label}{it.hint && <small>{it.hint}</small>}</span></a>
            : <button key={it.label} role="menuitem" className={it.danger ? "danger" : ""} disabled={it.disabled} onClick={() => { setOpen(false); it.onClick?.(); }}>{it.icon && <Icon name={it.icon} />}<span>{it.label}{it.hint && <small>{it.hint}</small>}</span></button>)}
        </div>
      )}
    </span>
  );
}

/** What this server allows: deployed servers require sign-in and onboard repos by upload or Git URL. */
export function useConfig() {
  return useAsync<Json>(() => api.get("/api/config"), []);
}

export const fmtBytes = (n: number) => n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : n >= 1024 ** 2 ? `${(n / 1024 ** 2).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`;

/** Copy a prompt that the server builds with the code, callers and test command in it (see prompts.py). */
export function CopyPrompt({ repo, kind, data, label = "Copy fix prompt" }: { repo: string; kind: string; data: Json; label?: string }) {
  const [state, setState] = useState<"" | "busy" | "done" | "err">("");
  const [text, setText] = useState("");
  const [tokens, setTokens] = useState(0);
  const go = async () => {
    setState("busy");
    try {
      const r = await api.post(`/api/repos/${repo}/prompt`, { kind, data });
      setTokens(r.tokens);
      try { await navigator.clipboard.writeText(r.prompt); setState("done"); setTimeout(() => setState(""), 2500); }
      catch { setText(r.prompt); setState(""); }     // no clipboard permission: show it to copy by hand
    } catch { setState("err"); setTimeout(() => setState(""), 2500); }
  };
  return (
    <>
      <button className="ghost sm" disabled={state === "busy"} onClick={go} title="A step-by-step prompt with the code, its callers and the test command: paste it into your coding agent">
        {state === "busy" ? "Building…" : state === "done" ? `Copied (~${tokens.toLocaleString()} tokens)` : state === "err" ? "Could not build it" : label}
      </button>
      {text && (
        <div className="palette-bg" onMouseDown={() => setText("")}>
          <div className="dialog" role="dialog" aria-label="Prompt" onMouseDown={(e) => e.stopPropagation()}>
            <div className="row" style={{ marginBottom: 10 }}><b>Prompt for your coding agent</b><span className="muted">~{tokens.toLocaleString()} tokens</span><span className="grow" /><button className="ghost sm" onClick={() => setText("")}>Close</button></div>
            <textarea readOnly value={text} onFocus={(e) => e.target.select()} autoFocus style={{ height: "50vh", fontFamily: "var(--mono)", fontSize: 12.5 }} />
          </div>
        </div>
      )}
    </>
  );
}
