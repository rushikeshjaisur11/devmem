import { useMemo, useState } from "react";
import { api, q } from "../api";
import { CyGraph, Empty, Err, useAsync } from "../components";
import { nav } from "../route";

export default function Lineage({ params }: { params: Record<string, string> }) {
  const [table, setTable] = useState(params.table || "");
  const [filter, setFilter] = useState("");
  const data = useAsync(() => api.get(`/api/lineage?${q({ table })}`), [table]);
  const tables: any[] = data.data?.tables || [];
  const cur = tables.find((t) => t.name === table);
  const elements = useMemo(() => {
    const els: any[] = [], seen = new Set<string>();
    const add = (id: string, label: string, kind: string) => { if (!seen.has(id)) { seen.add(id); els.push({ data: { id, label, kind } }); } };
    const shown = table ? tables.filter((t) => t.name === table || t.readers.concat(t.writers).some((j: any) => (cur?.readers.concat(cur.writers) || []).some((c: any) => c.repo === j.repo && c.job === j.job))) : tables;
    shown.forEach((t) => {
      add("t:" + t.name, t.name, "table");
      t.writers.forEach((w: any) => { const id = `j:${w.repo}:${w.job}`; add(id, `${w.repo}: ${w.job.split("#")[1] || w.job}`, "job"); els.push({ data: { id: `w${id}${t.name}`, source: id, target: "t:" + t.name, kind: "writes" } }); });
      t.readers.forEach((r: any) => { const id = `j:${r.repo}:${r.job}`; add(id, `${r.repo}: ${r.job.split("#")[1] || r.job}`, "job"); els.push({ data: { id: `r${id}${t.name}`, source: "t:" + t.name, target: id, kind: "reads" } }); });
    });
    return els;
  }, [JSON.stringify(tables), table]); // eslint-disable-line
  const shown = tables.filter((t) => !filter || t.name.includes(filter));
  return (
    <>
      <h2>Lineage</h2>
      <p className="sub">Which jobs read and write which tables, across repos; column-level derivation for the selected table.</p>
      <Err msg={data.error} />
      <div className="split" style={{ gridTemplateColumns: "280px minmax(0,1fr)" }}>
        <div className="card">
          <input placeholder="filter tables" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: "100%", marginBottom: 8 }} />
          <div className="list tree">{shown.map((t) => <a key={t.name} onClick={() => setTable(table === t.name ? "" : t.name)} style={{ fontWeight: t.name === table ? 700 : 400 }}>{t.name}<span className="muted"> {t.writers.length}w {t.readers.length}r</span></a>)}</div>
          {!shown.length && <Empty>No tables found. Index repos that read or write tables.</Empty>}
        </div>
        <div>
          {elements.length > 0 && <CyGraph elements={elements} layout="breadthfirst" onSelect={(d) => d.kind === "table" && setTable(d.label)} height={380} />}
          {cur && (
            <div className="card" style={{ marginTop: 12 }}>
              <h3>{cur.name}</h3>
              <div className="kv"><b>Columns</b><span className="mono">{cur.columns.length ? cur.columns.join(", ") : "unknown: import a schema snapshot (devmem-app schema import)"}</span>
                <b>Written by</b><span>{cur.writers.map((w: any) => `${w.repo}: ${w.job}`).join("; ") || "-"}</span>
                <b>Read by</b><span>{cur.readers.map((w: any) => `${w.repo}: ${w.job}`).join("; ") || "-"}</span></div>
              {data.data?.column_edges?.length > 0 && <table style={{ marginTop: 10 }}><thead><tr><th>Source column</th><th>→ Target column</th><th>Job</th></tr></thead><tbody>
                {data.data.column_edges.map((e: any, i: number) => <tr key={i} className="click" onClick={() => nav("impact", { target: e.src.split(".").slice(0, -1).join(".") === cur.name ? e.src : e.dst })}><td className="mono">{e.src}</td><td className="mono">{e.dst}</td><td className="mono">{e.job}</td></tr>)}
              </tbody></table>}
              {cur.columns.length > 0 && <div className="row" style={{ marginTop: 10 }}>{cur.columns.map((c: string) => <button key={c} onClick={() => nav("impact", { target: `${cur.name}.${c}` })}>impact of {c}</button>)}</div>}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
