import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import { Json } from "./api";

/* ---------- graph ---------- */
function cssVar(n: string) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

/** One source of truth for how each node/edge kind looks, used by both the canvas and the legends. */
const REPO_HUES = ["--accent", "--c-class", "--good", "--warn", "--bad"];

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
/** Layouts can leave repo boxes overlapping (cose sizes them before placing children): keep each repo's inner layout and set the boxes side by side. */
function packRepos(cy: cytoscape.Core, name: string, eles: cytoscape.Collection) {
  eles.layout({ ...(LAYOUTS[name] || LAYOUTS.cose), animate: false, fit: false } as any).run();
  const repos = eles.nodes("[kind = 'repo']").filter((r) => r.descendants().intersection(eles).length > 0).sort((a, b) => b.boundingBox({}).w * b.boundingBox({}).h - a.boundingBox({}).w * a.boundingBox({}).h);
  // wrap rows to the canvas' aspect ratio, so the boxes fill it instead of a thin strip
  const area = repos.toArray().reduce((s, r) => s + r.boundingBox({}).w * r.boundingBox({}).h, 0);
  const rowW = Math.max(...repos.map((r) => r.boundingBox({}).w), Math.sqrt(area * (cy.width() / Math.max(cy.height(), 1))));
  let x = 0, y = 0, rowH = 0;
  repos.forEach((r) => {
    const bb = r.boundingBox({});
    if (x > 0 && x + bb.w > rowW) { x = 0; y += rowH + 70; rowH = 0; }
    r.descendants().intersection(eles).shift({ x: x - bb.x1, y: y - bb.y1 });
    x += bb.w + 70; rowH = Math.max(rowH, bb.h);
  });
  cy.fit(eles, 30);
}
function runLayout(cy: cytoscape.Core, name: string, eles: cytoscape.Collection, animate = false) {
  if (name !== "columns" && eles.nodes("[kind = 'repo']").length > 1) return packRepos(cy, name, eles);
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
    // each repo gets its own hue; its modules are bubbles in that hue, sized by how many symbols they hold
    const repoHue = new Map(elements.filter((e) => e.data.kind === "repo").map((e, i) => [e.data.id, cssVar(REPO_HUES[i % REPO_HUES.length])]));
    const maxSize = Math.max(1, ...elements.map((e) => e.data.size || 0));
    const safe = elements.filter((e) => !e.data.source || (ids.has(e.data.source) && ids.has(e.data.target)))
      .map((e) => e.data.kind === "repo" || (e.data.kind === "module" && repoHue.has(e.data.parent)) ? { ...e, data: { ...e.data, hue: repoHue.get(e.data.kind === "repo" ? e.data.id : e.data.parent) } } : e);
    const hl = cssVar("--hl"), hlInk = cssVar("--hl-ink"), muted = cssVar("--text-3"), font = getComputedStyle(document.body).fontFamily;
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
        { selector: "node", style: { label: "data(label)", "font-family": font, "font-size": 13, "font-weight": 600, color: text, "background-color": text2, "background-opacity": 0.1, "border-width": 1.5, "border-color": line, "text-valign": "center", "text-halign": "center", shape: "round-rectangle", width: "label", height: "label", padding: "10px", "transition-property": "opacity, border-width", "transition-duration": 120 } as any },
        ...kindStyles,
        { selector: "node[kind = 'repo']", style: { "background-opacity": 0.03, "text-valign": "top", "text-margin-y": -4, "font-weight": 700, "font-size": 14, "border-style": "dashed", padding: "22px", width: "auto", height: "auto", shape: "round-rectangle" } as any },
        { selector: "node[kind = 'repo'][hue]", style: { "background-color": "data(hue)", "background-opacity": 0.05, "border-color": "data(hue)", "border-opacity": 0.45, "border-style": "solid", "border-width": 1.5, color: "data(hue)", "font-size": 17, "text-margin-y": -8, padding: "30px" } as any },
        { selector: "node[kind = 'module']", style: { "border-width": 1.5 } as any },
        { selector: "node[kind = 'module'][hue]", style: { shape: "ellipse", width: `mapData(size, 0, ${maxSize}, 18, 74)`, height: `mapData(size, 0, ${maxSize}, 18, 74)`, padding: "0px",
          "background-color": "data(hue)", "background-opacity": 0.22, "border-color": "data(hue)", "border-width": 2, "text-valign": "bottom", "text-margin-y": 6, "font-size": 12.5,
          "text-background-color": surface, "text-background-opacity": 0.85, "text-background-padding": "2px", "text-background-shape": "roundrectangle" } as any },
        { selector: "node[?focus]", style: { "background-color": hl, "background-opacity": 1, color: hlInk, "font-weight": 700, "border-width": 2.5, "border-color": text } as any },
        { selector: "edge", style: { width: 1.6, "line-color": line, "target-arrow-color": line, "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.8, opacity: 0.85 } as any },
        ...edgeStyles,
        { selector: "edge[conf = 'ambiguous']", style: { "line-style": "dotted", opacity: 0.55 } as any },
        { selector: "edge[weight]", style: { width: "mapData(weight, 1, 40, 1.2, 6)", "line-color": muted, "target-arrow-color": muted, opacity: 0.4 } as any },
        { selector: "edge.lbl", style: { label: "data(weight)", "font-size": 11, color: text2, "text-background-color": surface, "text-background-opacity": 0.9, "text-background-padding": "2px", "text-rotation": "autorotate" } as any },
        { selector: ".hidden", style: { display: "none" } as any },
        { selector: ".faded", style: { opacity: 0.12 } as any },
        { selector: ".dim", style: { opacity: 0.2 } as any },
        { selector: ".match", style: { "border-width": 3.5, "border-color": text, "background-color": hl, "background-opacity": 0.9 } as any },
        { selector: ":selected", style: { "border-width": 3.5, "border-color": text, "overlay-opacity": 0 } as any },
        { selector: "edge:selected", style: { width: 3, "line-color": accent, "target-arrow-color": accent, opacity: 1 } as any },
        { selector: "node.hov", style: { "border-width": 3, "z-index": 10 } as any },
        { selector: "edge.hov", style: { opacity: 1, "line-color": accent, "target-arrow-color": accent, "z-index": 10 } as any },
      ],
    }); } catch (e) { console.error("graph failed", e); return; }
    cy.on("tap", (ev) => { if (ev.target === cy) onSelect && onSelect(null); });
    cy.on("tap", "node", (ev) => onSelect && onSelect(ev.target.data()));
    cy.on("mouseover", "node", (ev) => { if (cy.scratch("q")) return; const nb = ev.target.closedNeighborhood(); cy.elements().not(nb).not(nb.ancestors()).addClass("dim"); nb.addClass("hov"); });
    cy.on("mouseout", "node", () => cy.elements().removeClass("dim hov"));
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
