import { useEffect, useRef } from "react";
import ForceGraph from "force-graph";

/** Obsidian-style map: every file (or symbol) as a dot sized by its links, a live force layout, labels that fade in as you zoom. */
export interface MapNode { id: string; label: string; kind: string; repo: string; file?: string; sid?: string; x?: number; y?: number; deg?: number }
export interface MapLink { source: string | MapNode; target: string | MapNode; kind: string; weight: number }
export interface MapSettings {
  colorBy: "repo" | "kind" | "folder"; arrows: boolean; textFade: number; nodeSize: number; linkWidth: number;
  center: number; repel: number; linkForce: number; linkDistance: number;
}
export const DEFAULT_SETTINGS: MapSettings = { colorBy: "repo", arrows: false, textFade: 1.2, nodeSize: 1, linkWidth: 1, center: 0.06, repel: 60, linkForce: 0.5, linkDistance: 40 };

// four hues at most on a screen: groups beyond the fourth fall back to grey
export const GROUP_HUES = ["--accent", "--accent-2", "--c-class", "--good"];
export const KIND_HUE: Record<string, string> = { file: "--text-3", function: "--text-3", method: "--text-3", class: "--accent-2", test: "--good", table: "--c-class" };

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const endId = (e: string | MapNode) => (typeof e === "string" ? e : e.id);
/** The folder a file sits in, per repo: the top level, or one deeper under wrappers like src/ ("api/(root)" at the repo root). */
const WRAPPERS = new Set(["src", "lib", "app", "packages", "source", "pkg", "apps", "services"]);
export const folderOf = (n: MapNode) => {
  const parts = (n.file || "").split("/").slice(0, -1);
  if (!parts.length) return `${n.repo}/(root)`;
  return `${n.repo}/${WRAPPERS.has(parts[0]) && parts.length > 1 ? parts.slice(0, 2).join("/") : parts[0]}`;
};
export const groupOf = (n: MapNode, by: MapSettings["colorBy"]) => (by === "repo" ? n.repo : by === "folder" ? folderOf(n) : n.kind);

export function ForceMap({ nodes, links, settings, groups, selected, onSelect, onReady }: {
  nodes: MapNode[]; links: MapLink[]; settings: MapSettings; groups: string[];
  selected: string | null; onSelect: (n: MapNode | null) => void; onReady?: (fg: any) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const fg = useRef<any>(null);
  const hover = useRef<{ id: string | null; nb: Set<string> }>({ id: null, nb: new Set() });
  const sel = useRef(selected); sel.current = selected;
  const cfg = useRef(settings); cfg.current = settings;
  const grp = useRef(groups); grp.current = groups;
  const colors = useRef<Record<string, string>>({});
  const adj = useRef(new Map<string, Set<string>>());

  const readColors = () => {
    const c: Record<string, string> = {};
    ["--text", "--text-2", "--text-3", "--line", "--line-2", "--surface", "--accent", ...GROUP_HUES, ...Object.values(KIND_HUE)].forEach((k) => (c[k] = css(k)));
    c.font = getComputedStyle(document.body).fontFamily;
    colors.current = c;
  };

  // build the canvas once
  useEffect(() => {
    readColors();
    const g = new ForceGraph<MapNode, MapLink>(el.current!)
      .backgroundColor("rgba(0,0,0,0)")
      .nodeId("id")
      .nodeLabel(() => "")
      .minZoom(0.05).maxZoom(12)
      .cooldownTicks(400)
      .linkColor((l: MapLink) => {
        const h = hover.current, c = colors.current;
        const s = endId(l.source), t = endId(l.target), focus = h.id || sel.current;
        if (focus && (s === focus || t === focus)) return c["--accent"];
        return focus ? c["--line"] : l.kind === "imports" ? c["--text-3"] : c["--line-2"];   // links between repos read darker
      })
      .linkWidth((l: MapLink) => {
        const focus = hover.current.id || sel.current, w = cfg.current.linkWidth * (0.6 + Math.min(Math.log2(l.weight || 1), 4) * 0.35) * (l.kind === "imports" ? 1.8 : 1);
        return focus && (endId(l.source) === focus || endId(l.target) === focus) ? w * 1.8 : w;
      })
      .linkLineDash((l: MapLink) => (l.kind === "tests" || l.kind === "reads" ? [2, 2] : null))
      .linkDirectionalArrowLength(() => (cfg.current.arrows ? 3.5 : 0))
      .linkDirectionalArrowRelPos(1)
      .nodeCanvasObject((n: MapNode, ctx: CanvasRenderingContext2D, scale: number) => {
        const c = colors.current, s = cfg.current, h = hover.current, focus = h.id || sel.current;
        const r = (2.2 + Math.sqrt(n.deg || 0) * 1.4) * s.nodeSize;
        const lit = !focus || n.id === focus || (adj.current.get(focus)?.has(n.id) ?? false);
        const gi = grp.current.indexOf(groupOf(n, s.colorBy));
        const hue = s.colorBy === "kind" ? c[KIND_HUE[n.kind] || "--text-3"] : n.kind === "table" ? c["--c-class"] : gi >= 0 && gi < GROUP_HUES.length ? c[GROUP_HUES[gi]] : c["--text-3"];
        ctx.globalAlpha = lit ? 1 : 0.14;
        ctx.beginPath();
        if (n.kind === "table") { ctx.moveTo(n.x!, n.y! - r * 1.2); ctx.lineTo(n.x! + r * 1.2, n.y!); ctx.lineTo(n.x!, n.y! + r * 1.2); ctx.lineTo(n.x! - r * 1.2, n.y!); ctx.closePath(); }
        else ctx.arc(n.x!, n.y!, r, 0, 2 * Math.PI);
        ctx.fillStyle = n.id === focus ? c["--accent"] : hue;
        ctx.fill();
        if (n.id === sel.current) { ctx.lineWidth = 1.5 / scale; ctx.strokeStyle = c["--text"]; ctx.stroke(); }
        // labels fade in with zoom; the focused node and its neighbours always show theirs
        const fade = Math.min(1, Math.max(0, (scale - s.textFade) / 0.8));
        const a = focus ? (lit ? 1 : 0) : fade;
        if (a > 0.02) {
          const fs = 11 / scale;
          ctx.globalAlpha = a * (lit ? 1 : 0.14);
          ctx.font = `${n.id === focus ? 600 : 400} ${fs}px ${c.font}`;
          ctx.textAlign = "center"; ctx.textBaseline = "top";
          ctx.fillStyle = n.id === focus ? c["--text"] : c["--text-2"];
          ctx.fillText(n.label, n.x!, n.y! + r + 2 / scale);
        }
        ctx.globalAlpha = 1;
      })
      .nodePointerAreaPaint((n: MapNode, color: string, ctx: CanvasRenderingContext2D) => {
        const r = (2.2 + Math.sqrt(n.deg || 0) * 1.4) * cfg.current.nodeSize + 2;
        ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x!, n.y!, r, 0, 2 * Math.PI); ctx.fill();
      })
      .onNodeHover((n: MapNode | null) => {
        hover.current = { id: n ? n.id : null, nb: n ? adj.current.get(n.id) || new Set() : new Set() };
        el.current!.style.cursor = n ? "pointer" : "";
      })
      .onNodeClick((n: MapNode) => onSelect(n))
      .onBackgroundClick(() => onSelect(null)) as any;
    // center force: pulls every node toward the origin, like Obsidian's "Center force"
    let ns: MapNode[] = [];
    const center = Object.assign((alpha: number) => { const k = cfg.current.center * alpha; for (const n of ns) { (n as any).vx -= n.x! * k; (n as any).vy -= n.y! * k; } }, { initialize: (x: MapNode[]) => { ns = x; } });
    g.d3Force("center", center);
    fg.current = g;
    const ro = new ResizeObserver(() => { g.width(el.current!.clientWidth).height(el.current!.clientHeight); });
    ro.observe(el.current!);
    const mo = new MutationObserver(readColors);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = window.matchMedia("(prefers-color-scheme: dark)"); mq.addEventListener("change", readColors);
    onReady && onReady(g);
    return () => { ro.disconnect(); mo.disconnect(); mq.removeEventListener("change", readColors); g._destructor(); };
  }, []); // eslint-disable-line

  // new node/link set: recompute degrees and adjacency, keep positions of nodes that stay
  useEffect(() => {
    const g = fg.current; if (!g) return;
    const a = new Map<string, Set<string>>();
    nodes.forEach((n) => a.set(n.id, new Set()));
    links.forEach((l) => { const s = endId(l.source), t = endId(l.target); a.get(s)?.add(t); a.get(t)?.add(s); });
    nodes.forEach((n) => (n.deg = a.get(n.id)!.size));
    adj.current = a;
    g.graphData({ nodes, links });
    const fresh = !nodes.some((n) => n.x !== undefined && (n as any).__seen);
    nodes.forEach((n) => ((n as any).__seen = true));
    if (fresh) g.onEngineStop(() => { g.zoomToFit(400, 40); g.onEngineStop(() => {}); });
  }, [nodes, links]);

  // forces
  useEffect(() => {
    const g = fg.current; if (!g) return;
    g.d3Force("charge").strength(-settings.repel).distanceMax(600);
    g.d3Force("link").distance(settings.linkDistance).strength(settings.linkForce);
    g.d3ReheatSimulation();
  }, [settings.repel, settings.linkDistance, settings.linkForce, settings.center]);

  // display-only changes: one repaint, no re-layout
  useEffect(() => {
    const g = fg.current; if (!g) return;
    g.linkDirectionalArrowLength(g.linkDirectionalArrowLength());
  }, [settings.arrows, settings.textFade, settings.nodeSize, settings.linkWidth, settings.colorBy, selected, groups.join(",")]);

  return <div ref={el} className="fmap" role="img" aria-label="Map of every file and the links between them. Use the list beside it for keyboard access." />;
}
