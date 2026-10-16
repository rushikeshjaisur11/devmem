import { useEffect, useState } from "react";
import { api, q } from "../api";
import { Empty, Err, RepoSelect, useAsync, useRepos } from "../components";

export default function Share({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [from, setFrom] = useState(params.repo || "");
  const [file, setFile] = useState(params.file || "");
  const [symbol, setSymbol] = useState(params.symbol || "");
  const [to, setTo] = useState("");
  const [dest, setDest] = useState("");
  const [append, setAppend] = useState(false);
  const [msg, setMsg] = useState<any>(null);
  const [err, setErr] = useState("");
  const fv = useAsync(() => (from && file ? api.get(`/api/repos/${from}/file?${q({ path: file })}`) : Promise.resolve(null)), [from, file]);
  const pv = useAsync(() => (from && file ? api.get(`/api/share/preview?${q({ repo: from, file, symbol })}`) : Promise.resolve(null)), [from, file, symbol]);
  useEffect(() => { if (file && !dest) setDest(file); }, [file]); // eslint-disable-line
  const targets = (repos.data || []).filter((r) => r.name !== from);
  return (
    <>
      <h2>Share</h2>
      <p className="sub">Copy a file or one function from a repo that already solved something into another repo. The copy gets an origin comment and is left unstaged for your review.</p>
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}>
          <b>From</b>{repos.data && <RepoSelect value={from} onChange={(v) => { setFrom(v); setFile(""); setSymbol(""); }} repos={repos.data} />}
          <input className="grow" placeholder="file path in that repo, e.g. app/retry.py (Explorer: “Copy to repo…”)" value={file} onChange={(e) => { setFile(e.target.value); setSymbol(""); }} aria-label="Source file" />
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Symbol"><option value="">whole file</option>{(fv.data?.symbols || []).filter((s: any) => s.kind !== "method").map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        </div>
        <div className="row">
          <b>To</b>
          <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Target repo"><option value="">choose…</option>{targets.map((r) => <option key={r.name}>{r.name}</option>)}</select>
          <input className="grow" placeholder="destination path in the target repo" value={dest} onChange={(e) => setDest(e.target.value)} aria-label="Destination path" />
          <label className="row"><input type="checkbox" checked={append} onChange={(e) => setAppend(e.target.checked)} /> append if it exists</label>
          <button className="primary" disabled={!from || !file || !to || !dest}
            onClick={async () => { setErr(""); setMsg(null); try { setMsg(await api.post("/api/share", { from_repo: from, file, symbol: symbol || undefined, to_repo: to, dest, append })); } catch (e: any) { setErr(e.message); } }}>Copy</button>
        </div>
      </div>
      <Err msg={err || pv.error} />
      {msg && <div className="card"><div className="ok">Wrote {msg.written} ({msg.note}).</div><div className="mono">{msg.origin}</div>
        {msg.review_imports.length > 0 && <><p className="muted">Check these imports from the source file exist in the target:</p><pre>{msg.review_imports.join("\n")}</pre></>}</div>}
      <div className="card"><h3>Preview</h3>{pv.data ? <pre style={{ margin: 0, overflow: "auto", maxHeight: 420 }}>{pv.data.text}</pre> : <Empty>Choose a file to preview what would be copied.</Empty>}</div>
    </>
  );
}
