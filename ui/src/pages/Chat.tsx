import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Badge, EditorLink, Err, Icon, editorHref, useAsync, useRepo, useRepos } from "../components";
import { nav } from "../route";

type Mode = "auto" | "ask" | "implement";
interface Msg { q: string; res?: any; err?: string; busy?: boolean; target?: string; force?: "ask" | "implement" }

const MODES: [Mode, string, string, string][] = [
  ["auto", "Auto", "Ask about your code, describe something to build, or paste a plan", "devmem decides: a question gets an answer with sources, a requirement or plan becomes a prompt for your coding agent."],
  ["ask", "Ask a question", "e.g. where do we validate email addresses?", "Find code, lessons and library docs that answer it, with file and line for each source."],
  ["implement", "Make an agent prompt", "e.g. add retry with backoff to the export job", "Describe the change, or paste a plan. You get a step-by-step prompt with the code to start from, current docs and what not to touch."],
];
const PLAN = "## Step 1: \n\n## Step 2: \n\nConstraints: \nDocs: ";
const KIND: Record<string, string> = { symbol: "code", test: "test", file: "file", table: "table", lesson: "lesson", rule: "rule", doc: "library docs", module: "module", overview: "overview" };
const KEY = "devmem-chat";

export default function Chat() {
  const repos = useRepos();
  const { repo } = useRepo();
  const status = useAsync(() => api.get("/api/chat/status"), []);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>("auto");
  const [llm, setLlm] = useState(false);
  const [code, setCode] = useState(true);
  const [everywhere, setEverywhere] = useState(true);
  const [msgs, setMsgs] = useState<Msg[]>(() => { try { return JSON.parse(sessionStorage.getItem(KEY) || "[]"); } catch { return []; } });
  const [copied, setCopied] = useState("");
  const [wide, setWide] = useState<Set<number>>(new Set());
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (status.data?.configured) setLlm(true); }, [status.data]);
  useEffect(() => { const el = box.current; if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 300) + "px"; } }, [text]);
  // the conversation survives moving to another page and back (this browser tab only)
  useEffect(() => { try { sessionStorage.setItem(KEY, JSON.stringify(msgs.filter((m) => !m.busy).slice(-20))); } catch { /* storage full or off */ } }, [msgs]);
  const settled = msgs.filter((m) => !m.busy).length;
  useEffect(() => { if (msgs.length) document.querySelector(".thread .turn:last-of-type")?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [msgs.length, settled]); // eslint-disable-line

  async function send(q: string, opts: { target?: string; force?: "ask" | "implement"; at?: number } = {}) {
    const question = q.trim();
    if (!question) return;
    const force = opts.force ?? (mode === "auto" ? undefined : mode);
    const target = opts.target ?? repo ?? undefined;
    const i = opts.at ?? msgs.length;
    if (opts.at === undefined) { setText(""); setMsgs((m) => [...m, { q: question, busy: true }]); }
    else setMsgs((m) => m.map((x, j) => (j === i ? { ...x, busy: true, err: undefined } : x)));
    try {
      const res = await api.post("/api/chat", { question, llm, detail: code ? "code" : "pointers", target, force, repos: everywhere || !repo ? undefined : [repo] });
      setMsgs((m) => m.map((x, j) => (j === i ? { q: question, res, target, force } : x)));
    } catch (e: any) {
      setMsgs((m) => m.map((x, j) => (j === i ? { q: question, err: e.message } : x)));
    }
  }
  const copy = (key: string, value: string) => navigator.clipboard?.writeText(value).then(() => { setCopied(key); setTimeout(() => setCopied(""), 1600); });
  const save = (name: string, value: string) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([value], { type: "text/markdown" })); a.download = name; a.click(); URL.revokeObjectURL(a.href); };
  const open = (h: any) => h.kind === "doc" ? window.open(h.path, "_blank", "noopener") : h.kind === "lesson" || h.kind === "rule" ? nav("memory", { repo: h.repo })
    : h.kind === "module" || h.kind === "overview" ? nav("analysis", { repo: h.repo }) : nav("impact", { repo: h.repo, target: h.ref });
  const openable = (h: any) => ["doc", "lesson", "rule", "symbol", "test", "module", "overview"].includes(h.kind);
  // starters taken from the user's own code, so the first click already shows something real
  const gaps = useAsync<any[]>(() => (repo ? api.get(`/api/repos/${repo}/gaps?limit=2`) : Promise.resolve([])), [repo]);
  const fn = gaps.data?.[0]?.id.split("#")[1];
  const starters: [Mode, string, string][] = [
    ["ask", "Find code", fn ? `where is ${fn} used and what does it do?` : "where is the retry logic?"],
    ["ask", "Understand", `what does ${repo || "this repo"} do and how is it organised?`],
    ["implement", "Change something", fn ? `add input validation and a test to ${fn}` : "add retry with backoff to the export job"],
  ];
  const current = MODES.find((m) => m[0] === mode)!;
  const indexed = (repos.data || []).filter((r) => r.indexed);

  return (
    <>
      <div className="row">
        <h1>Ask &amp; plan</h1>
        <span className="grow" />
        {msgs.length > 0 && <button className="ghost" onClick={() => { setMsgs([]); setWide(new Set()); }}><Icon name="trash" />Clear conversation</button>}
      </div>
      <p className="sub">Ask about your code and get an answer with the exact files and lines. Or describe a change and get a prompt to paste into your coding agent. Nothing here edits your repos.</p>
      {repos.data && !indexed.length && <div className="card callout"><div className="grow"><b>No repo is indexed yet.</b><div className="muted">Add one first; then you can ask about it here.</div></div><button className="primary" onClick={() => nav("repos")}><Icon name="plus" />Add a repo</button></div>}

      {msgs.length === 0 && indexed.length > 0 && (
        <div className="starters">
          {starters.map(([m, label, q]) => (
            <button key={q} className="starter" onClick={() => { setMode(m); send(q, { force: m === "auto" ? undefined : m }); }}>
              <span className="muted">{label}</span><b>{q}</b>
            </button>
          ))}
          <button className="starter" onClick={() => { setMode("implement"); setText(PLAN); box.current?.focus(); }}>
            <span className="muted">Hand over a plan</span><b>Start from a plan template: steps, constraints, docs to follow</b>
          </button>
        </div>
      )}

      <div className="thread">
        {msgs.map((m, idx) => {
          const r = m.res, impl = r?.intent === "implement";
          return (
            <div className="turn" key={idx}>
              <div className="turn-q"><span>{m.q}</span></div>
              <div className="card turn-a">
                {m.busy && <p className="muted" style={{ margin: 0 }}>Searching your code{llm ? " and writing an answer" : ""}…</p>}
                {m.err && <><Err msg={m.err} /><button className="sm" onClick={() => send(m.q, { at: idx, target: m.target, force: m.force })}><Icon name="refresh" />Try again</button></>}
                {r && (
                  <div className="turn-head">
                    <Badge kind={impl ? "warn" : ""}>{impl ? "Prompt for your agent" : "Answer"}</Badge>
                    <span className="muted">{r.reason}</span>
                    <span className="grow" />
                    <button className="ghost sm" onClick={() => send(m.q, { at: idx, target: m.target, force: impl ? "ask" : "implement" })}>{impl ? "Answer it as a question instead" : "Turn this into an agent prompt"}</button>
                  </div>
                )}
                {r?.needs_repo && (
                  <div>
                    <p style={{ margin: "6px 0 8px" }}>Which repo will the agent work in?</p>
                    <div className="chips">{r.needs_repo.map((n: string) => <button key={n} className="chip" onClick={() => send(m.q, { at: idx, target: n, force: "implement" })}>{n}</button>)}</div>
                  </div>
                )}
                {r && impl && !r.needs_repo && (
                  <>
                    <div className="turn-facts">
                      <span className="tag">repo <b>{r.target}</b></span>
                      <span className="tag">about <b>{r.tokens.toLocaleString()}</b> tokens</span>
                      <span className="tag"><b>{r.steps}</b> step{r.steps === 1 ? "" : "s"}</span>
                      <span className="tag" title="Code in your other repos that already does this"><b>{r.counts.org_solutions}</b> existing solution{r.counts.org_solutions === 1 ? "" : "s"}</span>
                      <span className="tag" title="Sections of current library docs included"><b>{r.counts.doc_excerpts}</b> docs excerpt{r.counts.doc_excerpts === 1 ? "" : "s"}</span>
                      {r.counts.modernize > 0 && <span className="tag warn"><b>{r.counts.modernize}</b> a library now provides</span>}
                      {(r.docs_fetched || []).map((d: any) => <span key={d.source} className={`tag ${d.status === "failed" ? "warn" : ""}`}>{d.name || d.source}: {d.status === "fetched" ? "docs fetched" : d.status === "cached" ? "using docs on file" : "docs not fetched"}</span>)}
                      <span className="grow" />
                      <button className="ghost sm" onClick={() => save(`${r.target}-task.md`, r.prompt)}><Icon name="download" />Save .md</button>
                      <button className="primary sm" onClick={() => copy("p" + idx, r.prompt)}><Icon name="check" />{copied === "p" + idx ? "Copied" : "Copy prompt"}</button>
                    </div>
                    {r.summary.flatMap((s: any) => s.modernize.map((x: string, i: number) => <p key={s.n + "-" + i} className="hint" style={{ margin: "0 0 6px" }}><Badge kind="warn">a library provides this</Badge> {x}</p>))}
                    {!r.counts.doc_excerpts && <p className="hint" style={{ margin: "0 0 8px" }}>No library docs matched this task. Add the libraries on <a onClick={() => nav("docs")}>Library docs</a> so the agent follows current APIs.</p>}
                    <pre className={`code prompt ${wide.has(idx) ? "open" : ""}`}>{r.prompt}</pre>
                    <div className="row" style={{ marginTop: 8 }}>
                      {r.prompt.split("\n").length > 22 && <button className="ghost sm" onClick={() => { const n = new Set(wide); n.has(idx) ? n.delete(idx) : n.add(idx); setWide(n); }}>{wide.has(idx) ? "Show less" : "Show the whole prompt"}</button>}
                      <span className="muted" style={{ fontSize: 12.5 }}>Paste it into your coding agent. devmem wrote nothing to your repos.</span>
                    </div>
                  </>
                )}
                {r && !impl && (
                  <>
                    <div className="answer">{r.answer}</div>
                    {r.llm_error && <p className="hint">The model could not be reached ({r.llm_error}); these are the matching sources.</p>}
                    <div className="row" style={{ margin: "10px 0 6px" }}>
                      <b style={{ fontSize: 13 }}>{r.hits.length ? `${r.hits.length} source${r.hits.length === 1 ? "" : "s"}` : "No sources"}</b>
                      <span className="muted" style={{ fontSize: 12.5 }}>{r.mode === "llm" ? `written by ${r.model} from these sources only` : status.data?.configured ? "matching code and docs" : <>matching code and docs. <a href="#/ai">Set up AI</a> for written answers (optional)</>}</span>
                      <span className="grow" />
                      {r.mode === "llm" && <button className="ghost sm" onClick={() => copy("a" + idx, r.answer)}>{copied === "a" + idx ? "Copied" : "Copy answer"}</button>}
                    </div>
                    <div className="sources">
                      {r.hits.map((h: any) => (
                        <details key={h.n} open={r.cited?.includes(h.n) || (r.mode !== "llm" && h.n === 1)}>
                          <summary>
                            <span className="src-n">{h.n}</span><span className="tag">{KIND[h.kind] || h.kind}</span><span className="src-title">{h.title}</span>
                            <span className="mono muted src-where">{h.kind === "doc" ? h.repo : `${h.repo}: ${h.path}${h.line > 1 ? ":" + h.line : ""}`}</span>
                            {h.kind !== "doc" && h.kind !== "module" && h.kind !== "overview" && <EditorLink href={editorHref(repos.data, h.repo, h.path, h.line)} />}
                          </summary>
                          {h.snippet && <pre className="code">{h.snippet}</pre>}
                          {openable(h) && <a className="btn-link" onClick={() => open(h)}>{h.kind === "doc" ? "Open the page" : h.kind === "lesson" || h.kind === "rule" ? "Open in Lessons & rules" : h.kind === "module" || h.kind === "overview" ? "Open AI analysis" : "See what depends on it"}</a>}
                        </details>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="composer card">
        <div className="seg" role="tablist" aria-label="What to do with your message">
          {MODES.map(([m, label, , tip]) => <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "on" : ""} title={tip} onClick={() => setMode(m)}>{label}</button>)}
        </div>
        <textarea ref={box} value={text} rows={2} placeholder={current[2]} aria-label="Message" disabled={!indexed.length}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
        <div className="composer-foot">
          <label className="row" title={`On: search every onboarded repo. Off: only ${repo || "the repo in the top bar"}.`}><input type="checkbox" checked={everywhere} onChange={(e) => setEverywhere(e.target.checked)} />Search all repos</label>
          <label className="row" title={status.data?.configured ? "The model writes the answer from the matching sources only and cites them." : "Optional. Without a model you get the matching sources."}><input type="checkbox" checked={llm} disabled={!status.data?.configured} onChange={(e) => setLlm(e.target.checked)} />Written answers{status.data?.configured ? ` (${status.data.model})` : <> (<a href="#/ai">set up AI</a>)</>}</label>
          <label className="row" title="On: prompts include the code to start from, its callers and exact commands, so a small model can follow them. Off: a shorter prompt for an agent that explores well on its own."><input type="checkbox" checked={code} onChange={(e) => setCode(e.target.checked)} />Step-by-step prompts</label>
          <span className="grow" />
          <span className="muted hide-sm">{mode !== "ask" && repo ? <>prompts are written for <b>{repo}</b> · </> : null}Enter to send, Shift+Enter for a new line</span>
          <button className="primary" disabled={!text.trim() || !indexed.length} onClick={() => send(text)}><Icon name="send" />Send</button>
        </div>
      </div>
    </>
  );
}
