import { useEffect, useState } from "react";
import { api } from "../api";
import { nav } from "../route";
import { Badge, Empty, Err, Icon, RepoSelect, useAsync, useRepos } from "../components";

const STEP: Record<string, string> = { describe: "Describing definitions", modules: "Summarising modules", overview: "Writing the overview" };

/** A text the model wrote that a person can correct in place. */
function Editable({ value, by, stale, placeholder, onSave }: { value: string; by: string; stale?: boolean; placeholder: string; onSave: (t: string) => Promise<void> }) {
  const [edit, setEdit] = useState(false);
  const [text, setText] = useState(value);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setText(value); setEdit(false); }, [value]);
  const save = async () => { setBusy(true); try { await onSave(text); } finally { setBusy(false); } };
  if (!edit) return (
    <div className="an-text">
      <span className={value ? "" : "muted"}>{value || placeholder}</span>
      <span className="an-meta">
        {by && <Badge>{by === "ai" ? "written by AI" : "written by a person"}</Badge>}
        {stale && <Badge kind="warn">code changed since</Badge>}
        <button className="ghost sm" onClick={() => setEdit(true)}>{value ? "Edit" : "Write"}</button>
      </span>
    </div>
  );
  return (
    <div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} autoFocus aria-label="Text" />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="primary sm" disabled={busy || text.trim() === value} onClick={save}><Icon name="check" />Save</button>
        <button className="ghost sm" disabled={busy} onClick={() => { setText(value); setEdit(false); }}>Cancel</button>
      </div>
    </div>
  );
}

export default function Analysis({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const [limit, setLimit] = useState(200);
  const [on, setOn] = useState<Record<string, boolean>>({ describe: true, modules: true, overview: true });
  const [err, setErr] = useState("");
  const d = useAsync<any>(() => repo ? api.get(`/api/repos/${repo}/analysis?limit=${limit}`) : Promise.resolve(null), [repo, limit]);
  const s = d.data;
  const job = s?.job;
  const running = job?.state === "running";
  useEffect(() => { if (!running) return; const t = setInterval(d.reload, 1200); return () => clearInterval(t); }, [running, d.reload]);
  useEffect(() => setErr(""), [repo]);

  const steps = ["describe", "modules", "overview"].filter((k) => on[k]);
  const todo = s ? (on.describe ? s.describe.todo : 0) + (on.modules ? s.modules.todo : 0) + (on.overview ? s.overview.todo : 0) : 0;
  const requests = s ? steps.reduce((n, k) => n + (s[k].todo ? s[k].requests : 0), 0) : 0;
  const tokens = s ? steps.reduce((n, k) => n + (s[k].todo ? s[k].tokens : 0), 0) : 0;
  const never = s && !s.last_run && !s.describe.described && !s.modules.written && !s.overview.text;

  const start = async () => { setErr(""); try { await api.post(`/api/repos/${repo}/analysis`, { steps, limit }); d.reload(); } catch (e: any) { setErr(e.message); } };
  const edit = (module: string | null) => async (text: string) => { setErr(""); try { await api.put(`/api/repos/${repo}/analysis`, { module, text }); d.reload(); } catch (e: any) { setErr(e.message); } };
  const check = (k: string, title: string, state: any, detail: string) => (
    <label className={`an-step ${on[k] ? "on" : ""}`}>
      <input type="checkbox" checked={on[k]} disabled={running} onChange={(e) => setOn({ ...on, [k]: e.target.checked })} />
      <div className="grow"><b>{title}</b><div className="muted">{detail}</div></div>
      {state}
    </label>
  );

  return (
    <>
      <div className="row"><h1>AI analysis</h1>{repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}</div>
      <p className="sub">The index (functions, calls, tests, tables) is built by parsers and needs no AI. This optional pass adds what parsers cannot: what each piece of code is <i>for</i>. Run it once after adding a repo, then again whenever you like; it only sends what is new or changed, and every text can be corrected by hand.</p>
      <Err msg={repos.error || d.error || err} />
      {repos.data && !repos.data.some((r) => r.indexed) && <div className="card"><Empty>No indexed repo yet. <a href="#/repos">Add one</a> first.</Empty></div>}

      {s && !s.llm.configured && (
        <div className="card callout">
          <div className="grow"><b>No AI model is set up, and you do not need one.</b><div className="muted">Search, impact and the code map work as they are. To run this analysis, connect a model once; or write the texts below by hand.</div></div>
          <button className="primary" onClick={() => nav("ai")}><Icon name="spark" />Set up AI</button>
        </div>
      )}

      {s && (
        <div className="card">
          <div className="row" style={{ marginBottom: 14 }}>
            <h3 style={{ margin: 0 }}>{never ? "First analysis" : "Run again for what changed"}</h3>
            {s.last_run && <span className="muted">last run {s.last_run.at} with {s.last_run.model} ({s.last_run.state})</span>}
          </div>
          <div className="an-steps">
            {check("describe", "Describe unclear definitions", s.describe.todo ? <Badge kind="warn">{s.describe.todo.toLocaleString()} to write</Badge> : <Badge kind="good">up to date</Badge>,
              `${s.describe.weak.toLocaleString()} of ${s.describe.total.toLocaleString()} functions and classes still have only a vague name or no docstring; ${s.describe.described.toLocaleString()} described so far. One sentence each, used by search.`)}
            {check("modules", "Summarise each module", s.modules.todo ? <Badge kind="warn">{s.modules.todo} to write</Badge> : <Badge kind="good">up to date</Badge>,
              `${s.modules.written} of ${s.modules.total} top-level modules have a summary. Two sentences each, published in the module cards agents read.`)}
            {check("overview", "Write the repo overview", s.overview.todo ? <Badge kind="warn">{s.overview.text ? "out of date" : "not written"}</Badge> : <Badge kind="good">up to date</Badge>,
              "One paragraph on what the repo does and how its parts fit, published in overview.md.")}
          </div>
          <div className="row" style={{ marginTop: 16 }}>
            {on.describe && s.describe.weak > 100 && (
              <label className="row" style={{ gap: 8 }}>Describe
                <select value={limit} disabled={running} onChange={(e) => setLimit(Number(e.target.value))} aria-label="How many definitions to describe">
                  <option value={100}>the 100 most used</option><option value={200}>the 200 most used</option><option value={500}>the 500 most used</option><option value={0}>all {s.describe.weak.toLocaleString()}</option>
                </select>
              </label>
            )}
            {running
              ? <button className="danger" onClick={() => api.del(`/api/repos/${repo}/analysis`).then(d.reload)}>Stop</button>
              : <button className="primary" disabled={!s.llm.configured || !todo || !steps.length} onClick={start}><Icon name="spark" />{todo ? `Run analysis${s.llm.model ? ` with ${s.llm.model}` : ""}` : "Everything is up to date"}</button>}
            {!running && todo > 0 && <span className="muted">about {requests.toLocaleString()} request{requests === 1 ? "" : "s"}, roughly {tokens.toLocaleString()} tokens of code sent{s.llm.configured ? "" : " (once a model is set up)"}</span>}
          </div>
          {job && (running || job.state !== "done" || job.failed > 0 || job.error) && (
            <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={job.total} aria-valuenow={job.done} aria-label="Analysis progress" style={{ marginTop: 16 }}>
              <div className="bar" style={{ width: `${job.total ? Math.round(100 * job.done / job.total) : 100}%` }} />
              <span>{running ? `${STEP[job.step] || "Starting"}: ${job.done.toLocaleString()} of ${job.total.toLocaleString()}` : job.state === "failed" ? `Stopped: ${job.error}` : job.state === "cancelled" ? "Stopped. What was written so far is kept." : "Finished."}
                {job.failed > 0 && job.state !== "failed" && ` ${job.failed} could not be written${job.error ? ` (${job.error})` : ""}; run again to retry them.`}</span>
            </div>
          )}
          {job && !running && job.state !== "failed" && (job.written.describe + job.written.modules + job.written.overview) > 0 && (
            <div className="ok">Wrote {job.written.describe} description{job.written.describe === 1 ? "" : "s"}, {job.written.modules} module summar{job.written.modules === 1 ? "y" : "ies"}{job.written.overview ? " and the overview" : ""}. A model can be wrong: <a href={`#/definitions?repo=${repo}`}>review the descriptions</a> and the texts below, then Publish the repo so agents get them.</div>
          )}
        </div>
      )}

      {s && (
        <div className="card">
          <h3>What this repo does</h3>
          <Editable value={s.overview.text} by={s.overview.by} stale={s.overview.stale} placeholder="Not written yet. Run the analysis, or write a paragraph yourself." onSave={edit(null)} />
        </div>
      )}
      {s && s.modules.items.length > 0 && (
        <div className="card">
          <h3>Modules</h3>
          <div className="an-mods">
            {s.modules.items.map((m: any) => (
              <div className="an-mod" key={m.name}>
                <span className="mono">{m.name}</span>
                <Editable value={m.text} by={m.by} stale={m.stale} placeholder="No summary yet." onSave={edit(m.name)} />
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
