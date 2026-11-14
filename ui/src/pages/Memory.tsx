import { useState } from "react";
import { api, q } from "../api";
import { Badge, Empty, Err, useRepo, useAsync, useRepos } from "../components";

function LessonForm({ initial, onSave, onCancel }: { initial: any; onSave: (v: any) => Promise<void>; onCancel: () => void }) {
  const [v, setV] = useState({ title: initial.title || "", problem: initial.problem || "", fix: initial.fix || "", tags: (initial.tags || []).join(", ") });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 8 }}><input className="grow" placeholder="Title" value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} aria-label="Title" /><input placeholder="tags, comma separated" value={v.tags} onChange={(e) => setV({ ...v, tags: e.target.value })} aria-label="Tags" /></div>
      <textarea placeholder="Problem: what went wrong, how it showed up" value={v.problem} onChange={(e) => setV({ ...v, problem: e.target.value })} aria-label="Problem" />
      <textarea placeholder="Fix: what to do instead" value={v.fix} onChange={(e) => setV({ ...v, fix: e.target.value })} aria-label="Fix" style={{ marginTop: 8 }} />
      <Err msg={err} />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="primary" disabled={busy || !v.title || !v.problem || !v.fix} onClick={async () => { setBusy(true); setErr(""); try { await onSave({ ...v, tags: v.tags.split(",").map((t: string) => t.trim()).filter(Boolean) }); } catch (e: any) { setErr(e.message); } setBusy(false); }}>Save</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function Memory(_: any) {
  const repos = useRepos();
  const mem = useAsync(() => api.get("/api/memory"), []);
  const [tab, setTab] = useState("lessons");
  const { repo } = useRepo();
  const [edit, setEdit] = useState<string>("");   // lesson id, or "new"
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [appliesTo, setAppliesTo] = useState<Record<string, string>>({});
  const [fixes, setFixes] = useState<Record<string, string>>({});
  const [rule, setRule] = useState({ text: "", applies_to: "**", severity: "rule", where: "repo" });
  const names: string[] = (repos.data || []).map((r) => r.name);
  const d = mem.data;
  const act = async (fn: () => Promise<any>, ok: (r: any) => string) => { setErr(""); setMsg(""); try { setMsg(ok(await fn())); mem.reload(); } catch (e: any) { setErr(e.message); } };
  const lessons: any[] = d && repo ? d.repos[repo]?.lessons || [] : [];
  const use = (id: string) => (d?.repos[repo]?.usefulness?.[id] as number[]) || [0, 0];
  const hubFor = (id: string) => d?.hub_lessons.find((l: any) => l.id === id);

  return (
    <>
      <div className="row" style={{ marginBottom: 6 }}>
        <h1 style={{ margin: 0 }}>Lessons &amp; rules</h1>
        <button onClick={() => act(() => api.post("/api/memory/sync"), (r) => `Synced: ${r.new_lessons} new lesson(s) to the hub, ${r.lessons_pushed} pushed to repos, ${r.new_feedback} feedback entries`)}>Sync hub</button>
        <button className="primary" onClick={() => { setTab("lessons"); setEdit("new"); }}>New lesson</button>
      </div>
      <p className="sub">Lessons are problems already solved; rules are standing instructions. Agents add lessons in their PRs; you curate here.</p>
      <Err msg={err || mem.error} />
      {msg && <div className="ok">{msg}</div>}
      <div className="tabs" role="tablist">
        {[["lessons", "Lessons"], ["rules", "Rules"], ["proposals", `Proposals${d?.proposals.length ? ` (${d.proposals.length})` : ""}`]].map(([id, l]) => <button key={id} role="tab" className={tab === id ? "on" : ""} onClick={() => setTab(id)}>{l}</button>)}
      </div>

      {tab === "lessons" && <>
        {edit === "new" && <LessonForm initial={{}} onCancel={() => setEdit("")} onSave={async (v) => { const r = await api.post(`/api/repos/${repo}/lessons`, v); setEdit(""); setMsg(r.status === "duplicate" ? `A similar lesson already exists (${r.id})` : `Saved ${r.id}`); mem.reload(); }} />}
        {!lessons.length && edit !== "new" && <Empty>No lessons for this repo yet. Agents add them with <code>devmem remember</code>; you can add one with “New lesson”.</Empty>}
        {lessons.map((l) => edit === l.id ? <LessonForm key={l.id} initial={l} onCancel={() => setEdit("")} onSave={async (v) => { await api.put(`/api/repos/${repo}/lessons/${l.id}`, v); setEdit(""); mem.reload(); }} /> : (
          <div className="card lesson" key={l.id}>
            <div className="row"><h4>{l.title}</h4>{(l.tags || []).map((t: string) => <span className="tag" key={t}>{t}</span>)}<span className="grow" /><span className="muted" title="How agents rated this lesson after recalling it">by {l.source} · rated useful {use(l.id)[0]}, not useful {use(l.id)[1]}</span><button className="quiet" onClick={() => setEdit(l.id)}>Edit</button></div>
            <dl><dt>Problem</dt><dd>{l.problem}</dd><dt>Fix</dt><dd>{l.fix}</dd>{(l.refs || []).length > 0 && <><dt>Code</dt><dd className="mono muted">{l.refs.join("  ")}</dd></>}</dl>
            <div className="row foot">
              <span className="muted">Shared with: {hubFor(l.id) ? (hubFor(l.id).repos || []).join(", ") : "not in the hub yet (Sync hub first)"}</span>
              {hubFor(l.id) && <>
                <select defaultValue="" onChange={(e) => e.target.value && act(() => api.post(`/api/memory/lessons/${l.id}/share`, e.target.value === "*" ? { everywhere: true } : { repos: [e.target.value] }), (r) => `Shared with ${r.repos.join(", ")}. Press Sync hub to deliver.`)} aria-label="Share with">
                  <option value="">Share with…</option><option value="*">all repos</option>{names.filter((n) => n !== repo).map((n) => <option key={n}>{n}</option>)}
                </select>
                <input placeholder="rule applies to (glob)" value={appliesTo[l.id] || ""} onChange={(e) => setAppliesTo({ ...appliesTo, [l.id]: e.target.value })} style={{ width: 170 }} aria-label="Applies to glob" />
                <button onClick={() => act(() => api.post(`/api/memory/lessons/${l.id}/to-rule`, { applies_to: appliesTo[l.id] || "**" }), () => "Promoted to a global rule. Press Sync hub to deliver.")}>Promote to rule</button>
              </>}
            </div>
          </div>
        ))}
      </>}

      {tab === "rules" && <>
        <div className="card">
          <h3>Add a rule</h3>
          <div className="rule-form">
            <label className="field">Rule<input value={rule.text} onChange={(e) => setRule({ ...rule, text: e.target.value })} placeholder="e.g. Never call the payments API from tests; use FakePayments" /></label>
            <label className="field">Applies to files<input className="mono" value={rule.applies_to} onChange={(e) => setRule({ ...rule, applies_to: e.target.value })} placeholder="** or tests/**" /></label>
            <label className="field">Strength<select value={rule.severity} onChange={(e) => setRule({ ...rule, severity: e.target.value })}><option value="rule">Must follow</option><option value="advice">Advice</option></select></label>
            <label className="field">Where<select value={rule.where} onChange={(e) => setRule({ ...rule, where: e.target.value })}><option value="repo">Only {repo || "this repo"}</option><option value="hub">Every repo (hub)</option></select></label>
            <button className="primary" disabled={!rule.text.trim() || (rule.where === "repo" && !repo)}
              onClick={() => act(() => api.post("/api/rules", { text: rule.text, applies_to: rule.applies_to || "**", severity: rule.severity, repo: rule.where === "repo" ? repo : undefined }),
                (r) => { setRule({ ...rule, text: "" }); return rule.where === "repo" ? `Added rule ${r.id} to ${repo}. Agents see it on their next recall; publish to update knowledge/rules.md.` : `Added global rule ${r.id}. Press Sync hub to deliver it to every repo.`; })}>Add rule</button>
          </div>
          <p className="hint" style={{ margin: "10px 0 0" }}>A rule on <code>**</code> is listed for every task; a narrower glob only comes up when an agent touches matching files. One-line rules can also go straight into <code>.devmem/rules.md</code>.</p>
        </div>
        <div className="card"><h3>Rules in {repo || "this repo"}</h3>
          {(d?.repos[repo]?.rules || []).length ? <table><thead><tr><th>Strength</th><th>Applies to</th><th>Rule</th><th>From</th><th></th></tr></thead><tbody>
            {d.repos[repo].rules.map((r: any) => <tr key={r.id}><td><Badge kind={r.severity === "rule" ? "warn" : ""}>{r.severity === "rule" ? "must" : "advice"}</Badge></td><td className="mono">{r.applies_to}</td><td>{r.text}</td><td className="muted">{r.id.startsWith("rules.md#") ? "rules.md" : r.scope === "global" ? "hub" : "this repo"}</td>
              <td>{!r.id.startsWith("rules.md#") && r.scope !== "global" && <button className="quiet danger" onClick={() => confirm(`Delete rule “${r.text.slice(0, 60)}”?`) && act(() => api.del(`/api/rules/${r.id}?${q({ repo })}`), () => "Rule deleted")}>Delete</button>}</td></tr>)}
          </tbody></table> : <Empty>No rules yet. Add one above, or promote a lesson.</Empty>}
        </div>
        <div className="card"><h3>Rules for every repo (hub)</h3>{d?.hub_rules.length ? <table><tbody>{d.hub_rules.map((r: any) => <tr key={r.id}><td style={{ width: 110 }}><Badge kind={r.severity === "rule" ? "warn" : ""}>{r.severity === "rule" ? "must" : "advice"}</Badge></td><td className="mono" style={{ width: 160 }}>{r.applies_to}</td><td>{r.text}</td>
          <td style={{ width: 90 }}><button className="quiet danger" onClick={() => confirm(`Delete global rule “${r.text.slice(0, 60)}”?`) && act(() => api.del(`/api/rules/${r.id}`), () => "Global rule deleted. Sync hub to remove it from repos.")}>Delete</button></td></tr>)}</tbody></table> : <Empty>None yet. Add one above with “Every repo”, or promote a lesson.</Empty>}</div>
      </>}

      {tab === "proposals" && (!d?.proposals.length ? <Empty>No proposals. Run <code>devmem-app memory ingest-reviews &lt;file&gt;</code> or <code>ingest-failures &lt;repo&gt;</code> to generate them.</Empty> :
        d.proposals.map((p: any) => (
          <div className="card" key={p.id}>
            <div className="row"><Badge kind="warn">{p.kind}</Badge><b>{p.text}</b></div>
            {p.kind === "rule" && <p className="muted">Seen {p.count} times in {p.prs} PRs · applies to <code>{p.applies_to}</code></p>}
            {p.evidence && <ul>{p.evidence.slice(0, 3).map((e: any, i: number) => <li key={i} className="muted">PR {e.pr} {e.path}: {e.body}</li>)}</ul>}
            {p.kind === "lesson" && <><p className="muted">{p.problem}</p><textarea placeholder="Fix (required to approve)" value={fixes[p.id] || ""} onChange={(e) => setFixes({ ...fixes, [p.id]: e.target.value })} aria-label="Fix" /></>}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="primary" onClick={() => act(() => api.post(`/api/memory/proposals/${p.id}/approve`, { repo: repo || undefined, fix: fixes[p.id] }), (r) => `Written ${r.path}`)}>Approve into {repo || "hub"}</button>
              <button className="danger" onClick={() => act(() => api.post(`/api/memory/proposals/${p.id}/reject`), () => "Rejected")}>Reject</button>
            </div>
          </div>
        )))}
    </>
  );
}
