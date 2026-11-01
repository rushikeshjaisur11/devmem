import { useEffect, useMemo, useState } from "react";
import { api, q } from "../api";
import { EditorLink, Empty, Err, editorHref, useAsync, useRepos } from "../components";
import { CyGraph, EDGE_KINDS, kindColor } from "../graph";
import { nav } from "../route";

const job = (j: any) => j.job.split("#")[1] || j.job;

export default function Lineage({ params }: { params: Record<string, string> }) {
  const [table, setTable] = useState<string | null>(params.table ?? null);   // null: not chosen yet; "": all tables
  const [filter, setFilter] = useState("");
  const repos = useRepos();
  const data = useAsync(() => api.get(`/api/lineage?${q({ table: table || undefined })}`), [table]);
  const tables: any[] = data.data?.tables || [];
  // the full map of many tables is unreadable at first sight: open on the busiest table
  useEffect(() => { if (table === null && tables.length) setTable(tables.length > 12 ? [...tables].sort((a, b) => b.readers.length + b.writers.length - a.readers.length - a.writers.length)[0].name : ""); }, [tables.length]); // eslint-disable-line
  const cur = tables.find((t) => t.name === table);
  const elements = useMemo(() => {
    const els: any[] = [], seen = new Set<string>();
    const add = (d: any) => { if (!seen.has(d.id)) { seen.add(d.id); els.push({ data: d }); } };
    const jid = (j: any) => `j:${j.repo}:${j.job}`;
    const addJob = (j: any) => add({ id: jid(j), label: `${j.repo} · ${job(j)}`, kind: "job", repo: j.repo, job: j.job });
    const writes = (j: any, t: any) => { addJob(j); els.push({ data: { id: `w${jid(j)}${t.name}`, source: jid(j), target: "t:" + t.name, kind: "writes" } }); };
    const reads = (t: any, j: any) => { addJob(j); els.push({ data: { id: `r${jid(j)}${t.name}`, source: "t:" + t.name, target: jid(j), kind: "reads" } }); };
    if (!cur) {
      tables.forEach((t) => { add({ id: "t:" + t.name, label: t.name, kind: "table" }); t.writers.forEach((w: any) => writes(w, t)); t.readers.forEach((r: any) => reads(t, r)); });
      return els;
    }
    // one hop each way: upstream tables -> jobs that write this table -> table -> jobs that read it -> tables those jobs write
    add({ id: "t:" + cur.name, label: cur.name, kind: "table", focus: true });
    const W = new Set(cur.writers.map(jid)), R = new Set(cur.readers.map(jid));
    cur.writers.forEach((w: any) => writes(w, cur));
    cur.readers.forEach((r: any) => reads(cur, r));
    tables.filter((t) => t !== cur).forEach((t) => {
      const up = t.readers.filter((j: any) => W.has(jid(j))), down = t.writers.filter((j: any) => R.has(jid(j)));
      if (up.length || down.length) add({ id: "t:" + t.name, label: t.name, kind: "table" });
      up.forEach((j: any) => reads(t, j));
      down.forEach((j: any) => writes(j, t));
    });
    return els;
  }, [JSON.stringify(tables), table]); // eslint-disable-line
  const shown = tables.filter((t) => !filter || t.name.toLowerCase().includes(filter.toLowerCase()));
  const Jobs = ({ list }: { list: any[] }) => list.length ? <>{list.map((j: any) => <div key={j.repo + j.job} className="lin-job"><a onClick={() => nav("impact", { repo: j.repo, target: j.job })} title="Check impact of this job"><span className="tag">{j.repo}</span> <span className="mono">{job(j)}</span></a><EditorLink href={editorHref(repos.data, j.repo, j.job.split("#")[0])} /></div>)}</> : <span className="muted">none indexed</span>;

  return (
    <>
      <h1>Data flow</h1>
      <p className="sub">Data flows left to right: the jobs that write a table, the table, and the jobs that read it, across repos. Pick a table to see its neighbourhood and column-level derivation.</p>
      <Err msg={data.error} />
      {!data.loading && !tables.length ? <div className="card"><Empty>No tables found yet. Lineage comes from SQL and Spark code that reads or writes tables; index a repo that has some.</Empty></div> : (
        <div className="lin">
          <div className="card lin-list">
            <input placeholder="Filter tables" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter tables" />
            <div>{shown.map((t) => (
              <button key={t.name} className={`lin-t ${t.name === table ? "on" : ""}`} aria-pressed={t.name === table} onClick={() => setTable(table === t.name ? "" : t.name)}>
                <span className="mono">{t.name}</span><span className="muted">{t.writers.length} in · {t.readers.length} out</span>
              </button>
            ))}</div>
            {!shown.length && <p className="hint">No table matches “{filter}”.</p>}
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="legend">
              <span><i style={{ background: kindColor("job") }} />job</span><span><i style={{ background: kindColor("table") }} />table</span>
              <span><i style={{ background: `var(${EDGE_KINDS.writes.color})` }} />writes</span><span><i style={{ background: `var(${EDGE_KINDS.reads.color})` }} />reads</span>
              <span className="muted">Click a table to focus it, a job to check its impact.</span>
            </div>
            {elements.length > 0 && <CyGraph elements={elements} layout="columns" height={460}
              onSelect={(d) => d && (d.kind === "table" ? setTable(d.label) : d.kind === "job" && nav("impact", { repo: d.repo, target: d.job }))} />}
            {cur && (
              <div className="card" style={{ marginTop: 16 }}>
                <div className="row" style={{ marginBottom: 12 }}><h3 className="mono" style={{ margin: 0 }}>{cur.name}</h3><span className="grow" /><button className="quiet" onClick={() => setTable("")}>Show all tables</button></div>
                <div className="lin-io">
                  <div><b>Written by</b><Jobs list={cur.writers} /></div>
                  <div><b>Read by</b><Jobs list={cur.readers} /></div>
                </div>
                <b className="lin-h">Columns</b>
                {cur.columns.length ? <div className="tags">{cur.columns.map((c: string) => <button key={c} className="chip" onClick={() => nav("impact", { target: `${cur.name}.${c}` })} title={`What breaks if ${c} changes`}>{c}</button>)}</div>
                  : <p className="hint">Column names unknown. Import a schema snapshot with <code>devmem-app schema import</code>.</p>}
                {data.data?.column_edges?.length > 0 && <>
                  <b className="lin-h">Column derivation</b>
                  <table><thead><tr><th>Source column</th><th>Target column</th><th>Job</th></tr></thead><tbody>
                    {data.data.column_edges.map((e: any, i: number) => <tr key={i} className="click" onClick={() => nav("impact", { target: e.src.split(".").slice(0, -1).join(".") === cur.name ? e.src : e.dst })}><td className="mono">{e.src}</td><td className="mono">{e.dst}</td><td className="mono">{e.job}</td></tr>)}
                  </tbody></table>
                </>}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
