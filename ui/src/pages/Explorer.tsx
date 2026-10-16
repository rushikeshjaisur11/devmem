import { useEffect, useRef, useState } from "react";
import { api, q } from "../api";
import { Empty, Err, RepoSelect, useAsync, useRepos, Badge } from "../components";
import { nav } from "../route";

function Tree({ node, depth, sel, onPick }: { node: any; depth: number; sel: string; onPick: (p: string) => void }) {
  const [open, setOpen] = useState(depth < 1 || sel.startsWith(node.path + "/"));
  if (node.type === "file")
    return <div className={`n ${sel === node.path ? "on" : ""}`} style={{ paddingLeft: 6 + depth * 12 }} onClick={() => onPick(node.path)} title={node.path}>{node.name}{node.symbols ? <span className="muted"> {node.symbols}</span> : null}</div>;
  return (
    <>
      {depth > 0 && <div className="n" style={{ paddingLeft: 6 + (depth - 1) * 12 }} onClick={() => setOpen(!open)}>{open ? "▾" : "▸"} {node.name}</div>}
      {(open || depth === 0) && node.children.map((c: any) => <Tree key={c.path} node={c} depth={depth + 1} sel={sel} onPick={onPick} />)}
    </>
  );
}

export default function Explorer({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const [file, setFile] = useState(params.file || (params.symbol ? params.symbol.split("#")[0] : ""));
  const [symId, setSymId] = useState(params.symbol || "");
  const [search, setSearch] = useState("");
  const [hits, setHits] = useState<any[]>([]);
  const codeRef = useRef<HTMLDivElement>(null);

  const tree = useAsync(() => (repo ? api.get(`/api/repos/${repo}/tree`) : Promise.resolve(null)), [repo]);
  const fv = useAsync(() => (repo && file ? api.get(`/api/repos/${repo}/file?${q({ path: file })}`) : Promise.resolve(null)), [repo, file]);
  const sym = useAsync(() => (repo && symId ? api.get(`/api/repos/${repo}/symbol?${q({ id: symId })}`) : Promise.resolve(null)), [repo, symId]);

  useEffect(() => {
    if (!repo || search.trim().length < 2) { setHits([]); return; }
    const t = setTimeout(() => api.get(`/api/repos/${repo}/search?${q({ q: search })}`).then(setHits).catch(() => setHits([])), 200);
    return () => clearTimeout(t);
  }, [repo, search]);

  useEffect(() => { // scroll to selected symbol
    if (sym.data && codeRef.current) {
      const el = codeRef.current.querySelector(`[data-ln="${sym.data.symbol.line}"]`);
      el && (el as HTMLElement).scrollIntoView({ block: "center" });
    }
  }, [sym.data, fv.data]);

  const pick = (id: string) => { setSymId(id); const f = id.split("#")[0]; if (f !== file) setFile(f); };
  const lines: string[] = fv.data ? fv.data.text.split("\n") : [];
  const s = sym.data?.symbol;
  const issuesByLine: Record<number, any[]> = {};
  (fv.data?.issues || []).forEach((i: any) => (issuesByLine[i.line] = [...(issuesByLine[i.line] || []), i]));

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Explorer</h2>
        {repos.data && <RepoSelect value={repo} onChange={(v) => { setRepo(v); setFile(""); setSymId(""); }} repos={repos.data} />}
        <input className="grow" placeholder="Find a symbol or concept (e.g. retry logic)" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search symbols" />
      </div>
      <Err msg={tree.error} />
      {hits.length > 0 && (
        <div className="card list">{hits.map((h) => <a key={h.id} onClick={() => { pick(h.id); setSearch(""); }}><span className="mono">{h.file}:{h.line}</span> {h.kind} <b>{h.qualname}</b> <span className="muted">{h.doc}</span></a>)}</div>
      )}
      <div className="split">
        <div className="card tree">{tree.data ? <Tree node={tree.data} depth={0} sel={file} onPick={(p) => { setFile(p); setSymId(""); }} /> : <Empty>{tree.loading ? "Loading…" : "Pick a repo"}</Empty>}</div>
        <div>
          {!file ? <Empty>Pick a file, or search for a symbol.</Empty> : fv.error ? <Err msg={fv.error} /> : (
            <>
              <div className="row" style={{ marginBottom: 6 }}><b className="mono">{file}</b>{fv.data && <Badge>{fv.data.lang}</Badge>}{fv.data?.issues?.length ? <Badge kind="warn">{fv.data.issues.length} hygiene issue(s)</Badge> : null}</div>
              <div className="code" ref={codeRef}>
                {lines.map((l, i) => {
                  const n = i + 1, inSym = s && s.file === file && n >= s.line && n <= s.end_line, iss = issuesByLine[n];
                  return <div key={n} data-ln={n} className={`ln ${inSym ? "hl" : ""}`}><span className="no">{n}</span><span className="mk" title={iss?.map((x) => `${x.tool} ${x.code}: ${x.msg}`).join("\n")}>{iss ? "!" : ""}</span><span>{l || " "}</span></div>;
                })}
              </div>
            </>
          )}
        </div>
        <div>
          {fv.data?.symbols?.length ? (
            <div className="card list"><h3>Outline</h3>{fv.data.symbols.map((x: any) => <a key={x.id} onClick={() => pick(x.id)} style={{ paddingLeft: x.kind === "method" ? 14 : 0, fontWeight: x.id === symId ? 700 : 400 }}>{x.kind === "class" ? "class " : ""}{x.name}<span className="muted"> :{x.line}</span></a>)}</div>
          ) : null}
          {s && sym.data && (
            <div className="card">
              <h3>{s.qualname}</h3>
              <div className="mono" style={{ marginBottom: 8 }}>{s.sig}</div>
              {s.doc && <p className="muted">{s.doc}</p>}
              <div className="row" style={{ marginBottom: 8 }}>
                <button onClick={() => nav("impact", { repo, target: s.id })}>Impact</button>
                <button onClick={() => nav("graph", { repo, focus: s.id })}>Graph</button>
                <button onClick={() => nav("share", { repo, file: s.file, symbol: s.id })}>Copy to repo…</button>
              </div>
              <div className="list"><b>Called by</b>{sym.data.callers.length ? sym.data.callers.map((c: any) => <a key={c.id + c.type} onClick={() => pick(c.id)}>{c.id.split("#")[1] || c.id} <span className="muted">{c.type === "tests" ? "test" : c.conf}</span></a>) : <div className="muted">nobody in this repo</div>}</div>
              <div className="list" style={{ marginTop: 8 }}><b>Calls</b>{sym.data.callees.filter((c: any) => c.type === "calls").length ? sym.data.callees.filter((c: any) => c.type === "calls").map((c: any) => <a key={c.id} onClick={() => pick(c.id)}>{c.id.split("#")[1]}</a>) : <div className="muted">nothing indexed</div>}</div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
