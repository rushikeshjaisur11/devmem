import { useState } from "react";
import { api } from "../api";
import { Badge, Empty, Err, LineChart, RepoSelect, useAsync, useRepos } from "../components";
import { nav } from "../route";

export default function Metrics({ params }: { params: Record<string, string> }) {
  const repos = useRepos();
  const [repo, setRepo] = useState(params.repo || "");
  const m = useAsync(() => (repo ? api.get(`/api/repos/${repo}/metrics`) : Promise.resolve(null)), [repo]);
  const d = m.data;
  const trend = (d?.hygiene_trend || []).map((t: any) => ({ x: t.ts.slice(0, 10), y: Object.values(t.counts as Record<string, number>).reduce((a, b) => a + b, 0) }));
  return (
    <>
      <div className="row" style={{ marginBottom: 6 }}><h2 style={{ margin: 0 }}>Metrics</h2>{repos.data && <RepoSelect value={repo} onChange={setRepo} repos={repos.data} />}</div>
      <p className="sub">Repo health and knowledge quality. Per-PR token and diff-size trends arrive with the metrics loop.</p>
      <Err msg={m.error} />
      {d && <>
        <div className="card">
          {[["symbols", d.symbols], ["tests", d.tests], ["files", d.files], ["lessons", d.lessons], ["rules", d.rules], ["recalls rated useful", d.feedback.useful], ["rated useless", d.feedback.useless]].map(([k, v]) => <span className="stat" key={k as string}><b>{v}</b><span>{k}</span></span>)}
        </div>
        <div className="card"><h3>Knowledge freshness</h3>
          {d.knowledge_problems.length === 0 ? <Badge kind="good">all knowledge files fresh and within budget</Badge> : <ul style={{ margin: 0, paddingLeft: 18 }}>{d.knowledge_problems.map((p: string) => <li key={p}>{p}</li>)}</ul>}
        </div>
        <div className="card"><h3>Hygiene findings over runs</h3><LineChart points={trend} label="Hygiene findings" /></div>
        <div className="card"><h3>Hotspots (change carefully)</h3>
          {d.hotspots.length ? <table><thead><tr><th>File</th><th className="num">Commits</th><th className="num">Bug fixes</th><th className="num">Authors</th><th>Last change</th></tr></thead><tbody>
            {d.hotspots.map((h: any) => <tr key={h.file} className="click" onClick={() => nav("explorer", { repo, file: h.file })}><td className="mono">{h.file}</td><td className="num">{h.commits}</td><td className="num">{h.bugfix}</td><td className="num">{h.authors}</td><td>{h.last}</td></tr>)}
          </tbody></table> : <Empty>No hotspots yet: publish a repo that has git history.</Empty>}
        </div>
        <div className="card"><h3>PR metrics</h3><Empty>Diff size, files touched, review rounds and agent token use per PR will appear here once the metrics loop is added.</Empty></div>
      </>}
    </>
  );
}
