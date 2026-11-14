import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { nav } from "../route";
import { Badge, CopyPrompt, EditorLink, Empty, Err, Icon, editorHref, useAsync, useRepo, useRepos } from "../components";

const STATUS: Record<string, [string, string, string]> = {
  ready: ["Ready now", "good", "The replacement is already available to this repo."],
  upgrade: ["Needs an upgrade", "warn", "The repo's declared language version is older than the replacement needs."],
  "new-dependency": ["New dependency", "", "A library the repo does not use yet. Usually only worth adding if this code has to grow."],
};
const CONF: Record<string, [string, string]> = { high: ["clear match", "good"], medium: ["likely", "warn"], low: ["name match only", ""] };

export default function Modernize(_: any) {
  const repos = useRepos();
  const { repo } = useRepo();
  const d0 = useAsync<any>(() => (repo ? api.get(`/api/repos/${repo}/modernize`) : Promise.resolve(null)), [repo]);
  const [fresh, setFresh] = useState<any>(null);
  const d = fresh || d0.data;
  const [show, setShow] = useState("ready");
  const [text, setText] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [latest, setLatest] = useState(true);
  useEffect(() => { setFresh(null); setSel(new Set()); setMsg(""); setErr(""); setShow("ready"); setText(""); }, [repo]);

  const scan = async () => { setBusy("scan"); setErr(""); setMsg(""); try { const r = await api.post(`/api/repos/${repo}/modernize`, { latest }); setFresh(r);
    const li = r.latest_info || {};
    setMsg(`Scanned ${r.scanned.toLocaleString()} functions and classes: ${r.findings.length} candidate${r.findings.length === 1 ? "" : "s"}.`
      + (li.docs_fetched?.length ? ` Fetched the latest docs for ${li.docs_fetched.map((x: any) => `${x.name} ${x.version}`.trim()).join(", ")}.` : "")
      + (li.error ? ` ${li.error}.` : "") + (li.notes_error ? ` Note: ${li.notes_error}.` : "")); } catch (e: any) { setErr(e.message); } setBusy(""); };
  const verify = async () => {
    setBusy("ai"); setErr(""); setMsg("");
    try { const r = await api.post(`/api/repos/${repo}/modernize/ai`, { keys: [...sel].slice(0, 20) }); setMsg(`${r.checked} checked by the model.${r.error ? ` Some failed: ${r.error}` : ""}`); setSel(new Set()); setFresh(await api.get(`/api/repos/${repo}/modernize`)); } catch (e: any) { setErr(e.message); }
    setBusy("");
  };
  const all: any[] = d?.findings || [];
  const list = useMemo(() => { const q = text.trim().toLowerCase(); return all.filter((f) => (show === "all" || f.status === show) && (!q || `${f.file} ${f.name} ${f.what} ${f.use}`.toLowerCase().includes(q))); }, [all, show, text]);
  const key = (f: any) => `${f.id}|${f.rule}`;
  const rt = d?.runtime || {};

  return (
    <>
      <div className="row">
        <h1>Modernize</h1>
        <span className="grow" />
        <label className="row" title="Checks the package registries for the newest release of every library this repo uses, downloads the current docs for libraries that have none on file, and reads the release notes since the version in use. Needs internet on the machine that runs devmem."><input type="checkbox" checked={latest} disabled={!!busy} onChange={(e) => setLatest(e.target.checked)} />Compare with the latest releases and docs</label>
        <button className={d?.available ? "" : "primary"} disabled={!repo || !!busy} onClick={scan}><span className={busy === "scan" ? "spin" : ""}><Icon name="refresh" /></span>{busy === "scan" ? (latest ? "Fetching latest and scanning…" : "Scanning…") : d?.available ? "Scan again" : "Scan this repo"}</button>
      </div>
      <p className="sub">Finds code written by hand that the language, a library you already use, or a well-known library now provides: a retry loop, a dict used as a cache, a JSON deep copy. Less code to own, and agents stop copying the old pattern. The scan reads the code and needs no AI; nothing is changed.</p>
      <Err msg={repos.error || d0.error || err} />
      {msg && <div className="ok">{msg}</div>}
      {d && !d.available && <div className="card"><Empty>Not scanned yet. The scan takes a few seconds and only reads the code.</Empty></div>}

      {d?.available && (
        <>
          <div className="card">
            <div className="kpis" style={{ marginBottom: 0 }}>
              <div className="kpi"><span className="k">Ready now</span><b className={d.counts.ready ? "good" : ""}>{d.counts.ready}</b><span className="t">about {d.lines.toLocaleString()} lines in those functions</span></div>
              <div className="kpi"><span className="k">Needs an upgrade</span><b>{d.counts.upgrade}</b><span className="t">language version too old</span></div>
              <div className="kpi"><span className="k">New dependency</span><b>{d.counts["new-dependency"]}</b><span className="t">a library not used yet</span></div>
              <div className="kpi"><span className="k">Libraries behind</span><b>{d.behind.length}</b><span className="t">newer release available</span></div>
            </div>
            <p className="hint" style={{ margin: "14px 0 0" }}>
              Declared versions: Python {rt.python || "not declared"}, Node.js {rt.node || "not declared"}. {d.libraries ?? 0} libraries declared, current docs on file for {d.docs_on_file ?? 0}. Scanned {d.scanned.toLocaleString()} functions and classes on {d.ts.slice(0, 16).replace("T", " ")}; the {d.docs_checked.toLocaleString()} most used were also compared with the <a href="#/docs">library docs on file</a>.
              These are candidates: a match means the code looks like the pattern, not that the replacement covers every case.
            </p>
          </div>

          <div className="card">
            <div className="toolbar">
              <div className="seg" role="tablist" aria-label="Which candidates">
                {(["ready", "upgrade", "new-dependency", "all"] as const).map((k) => (
                  <button key={k} role="tab" aria-selected={show === k} className={show === k ? "on" : ""} title={STATUS[k]?.[2]} onClick={() => setShow(k)}>{k === "all" ? "All" : STATUS[k][0]} ({k === "all" ? all.length : d.counts[k]})</button>
                ))}
              </div>
              <input className="grow" style={{ maxWidth: 300 }} placeholder="Filter by file, function or pattern" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter" />
              <span className="toolbar-end">
                {sel.size > 0 && <button className="ghost" onClick={() => setSel(new Set())}>Clear selection</button>}
                {d.llm?.configured
                  ? <button disabled={!!busy || !sel.size} onClick={verify} title="Sends the selected functions to the model, which confirms or rejects each match and says what the replacement would not cover"><Icon name="spark" />{busy === "ai" ? "Checking…" : `Double-check ${Math.min(sel.size, 20) || ""} with ${d.llm.model}`}</button>
                  : <button className="ghost" onClick={() => nav("ai")} title="Optional: a model can confirm or reject each match">Set up AI to double-check</button>}
              </span>
            </div>
            {!list.length ? <Empty>{all.length ? "Nothing in this group." : "No hand-written patterns found. Either the code already uses the built-ins, or it does things the catalog does not know."}</Empty> : (
              <div className="finds">
                {list.slice(0, 150).map((f) => (
                  <div className="find mz" key={key(f)}>
                    <input type="checkbox" checked={sel.has(key(f))} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(key(f)) : n.delete(key(f)); setSel(n); }} aria-label={`Select ${f.name}`} />
                    <div className="find-main">
                      <div className="find-where"><span className="mono sym">{f.name}</span><span className="mono muted">{f.file}:{f.line}</span><EditorLink href={editorHref(repos.data, repo, f.file, f.line)} /></div>
                      <div className="mz-swap"><span><span className="muted">Hand-written:</span> {f.what}</span><span className="mz-arrow" aria-hidden="true">→</span><span><span className="muted">Use:</span> <Md text={f.use} /></span></div>
                      {f.evidence && <pre className="code mz-ev">{f.evidence}</pre>}
                      <div className="tags">
                        <Badge kind={STATUS[f.status][1]}>{STATUS[f.status][0]}</Badge>
                        <Badge kind={CONF[f.confidence][1]}>{CONF[f.confidence][0]}</Badge>
                        <span className="tag">from {f.source}</span>
                        {f.callers > 0 && <span className="tag">used by <b>{f.callers}</b></span>}
                        {f.ai && <Badge kind={f.ai.verdict === "yes" ? "good" : "bad"}>{f.ai.verdict === "yes" ? "model agrees" : "model disagrees"}</Badge>}
                      </div>
                      <div className="hint">Status: {f.needs}.{f.ai?.note && f.ai.note.toLowerCase() !== "nothing" ? ` Model's note: ${f.ai.note}` : ""}</div>
                    </div>
                    <div className="find-act">
                      <CopyPrompt repo={repo} kind="modernize" data={f} label="Copy replace prompt" />
                      {f.url && /^https?:/.test(f.url) && <a className="btn-link" href={f.url} target="_blank" rel="noreferrer">Read the docs</a>}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {list.length > 150 && <p className="hint" style={{ marginTop: 12 }}>Showing the first 150. Narrow with the filter.</p>}
          </div>

          {d.behind.length > 0 && (
            <div className="card">
              <h3>Libraries behind their latest release</h3>
              <p className="hint" style={{ marginTop: -8 }}>Code written around an older release often does by hand what a newer one includes. For each library: what changed since your version, and the functions here that use it. Read the highlights against those functions before upgrading.</p>
              <div className="libs">
                {d.behind.map((l: any) => (
                  <div className="lib" key={l.eco + l.name}>
                    <div className="lib-head">
                      <b>{l.name}</b><span className="muted">{l.eco}</span>
                      <span className="mono">{l.using || "any"}</span><span className="mz-arrow" aria-hidden="true">→</span><span className="mono">{l.latest}</span>
                      <Badge kind={l.behind === "major" ? "warn" : ""}>{l.behind === "major" ? "a major version behind" : "minor versions behind"}</Badge>
                      {l.docs ? <Badge kind="good">latest docs on file</Badge> : <a onClick={() => nav("docs")}>add its docs</a>}
                      <span className="grow" />
                      {l.notes_url && <a className="btn-link" href={l.notes_url} target="_blank" rel="noreferrer">Release notes</a>}
                    </div>
                    {l.highlights.length > 0 && <ul className="plain lib-news">{l.highlights.map((h: string, i: number) => <li key={i}><span className="mono">{h.split(": ")[0]}</span> {h.split(": ").slice(1).join(": ")}</li>)}</ul>}
                    <div className="lib-use">
                      {l.functions ? <><span className="muted">Used by {l.functions} function{l.functions === 1 ? "" : "s"} here{l.functions > l.top.length ? ", most called first" : ""}:</span>{l.top.map((x: any) => <span className="tag mono" key={x.id} title={`${x.file}:${x.line}`}>{x.name} <EditorLink href={editorHref(repos.data, repo, x.file, x.line)} /></span>)}</>
                        : <span className="muted">No function in the indexed code imports it directly.</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}

/** `code` spans in a short string. */
const Md = ({ text }: { text: string }) => <>{text.split("`").map((t, i) => i % 2 ? <code key={i}>{t}</code> : <span key={i}>{t}</span>)}</>;
