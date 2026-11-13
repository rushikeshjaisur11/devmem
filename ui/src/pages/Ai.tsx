import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Err, Icon, useAsync } from "../components";

const HELP: Record<string, { key: string; where: string; base?: string }> = {
  anthropic: { key: "Anthropic API key", where: "Create one in the Anthropic Console under API keys." },
  openai: { key: "API key (leave empty for a local server)", where: "OpenAI: create a key under API keys. Ollama or LM Studio: no key, only the server address below.", base: "Leave empty for OpenAI; http://localhost:11434/v1 for Ollama" },
  gemini: { key: "Gemini API key", where: "Create one in Google AI Studio under Get API key." },
  vertex: { key: "Vertex API key (or leave empty and fill in the project)", where: "Needs the google-genai package on the machine that runs devmem: pip install google-genai." },
};

export default function Ai(_: any) {
  const st = useAsync<any>(() => api.get("/api/settings/ai"), []);
  const s = st.data;
  const [f, setF] = useState<any>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState("");
  const [test, setTest] = useState<any>(null);
  useEffect(() => { if (s && !f) setF({ provider: s.provider || "", model: s.model || "", base_url: s.base_url || "", embed_model: s.embed_model || "", project: s.project || "", location: s.location || "", local_embed: s.local_embed && s.local_embed !== "0" }); }, [s]); // eslint-disable-line
  if (!s || !f) return <><h1>AI setup</h1><Err msg={st.error} />{!st.error && <p className="muted">Loading…</p>}</>;

  const prov = s.providers.find((p: any) => p.id === f.provider);
  const fromEnv = (k: string) => s.from_environment.includes(k);
  const set = (k: string, v: any) => { setF({ ...f, [k]: v }); setSaved(""); setTest(null); };
  const pick = (id: string) => { const p = s.providers.find((x: any) => x.id === id); setF({ ...f, provider: id, model: id === s.provider ? s.model : p?.model || "", embed_model: id === s.provider ? s.embed_model : "", base_url: id === s.provider ? s.base_url : "" }); setKey(""); setSaved(""); setTest(null); };
  const payload = () => ({ ...f, local_embed: f.local_embed ? "1" : "", api_key: key || undefined });

  async function save(andTest: boolean) {
    setBusy(andTest ? "test" : "save"); setErr(""); setSaved(""); setTest(null);
    try {
      await api.put("/api/settings/ai", payload());
      setKey(""); st.reload(); setSaved("Saved.");
      if (andTest) setTest(await api.post("/api/settings/ai/test"));
    } catch (e: any) { setErr(e.message); }
    setBusy("");
  }
  async function turnOff() {
    setBusy("off"); setErr("");
    try { await api.put("/api/settings/ai", { provider: "", clear_key: true }); setF({ ...f, provider: "" }); setKey(""); st.reload(); setSaved("AI is off. Everything keeps working with keyword search."); setTest(null); } catch (e: any) { setErr(e.message); }
    setBusy("");
  }

  return (
    <>
      <div className="row"><h1>AI setup</h1>{s.configured ? <Badge kind="good">connected: {s.model}</Badge> : <Badge>off: not needed</Badge>}</div>
      <p className="sub">devmem indexes, maps and searches your code without any AI. A model is optional and adds three things: written answers in Ask &amp; plan, search by meaning, and descriptions for code that has none (Improve descriptions). Set it up here once; no environment variables or restart needed.</p>
      <Err msg={st.error || err} />

      <div className="card">
        <h3>1 · Choose a model provider</h3>
        <div className="choices">
          {s.providers.map((p: any) => (
            <button key={p.id} className={`choice ${f.provider === p.id ? "on" : ""}`} aria-pressed={f.provider === p.id} disabled={fromEnv("provider")} onClick={() => pick(p.id)}>
              <b>{p.label.split(" (")[0].split(" or any")[0]}</b>
              <span>{p.id === "anthropic" ? "API key" : p.id === "openai" ? "OpenAI, Ollama, LM Studio, OpenRouter, Azure" : p.id === "gemini" ? "AI Studio API key" : "API key, or project and gcloud login"}</span>
            </button>
          ))}
        </div>
        {fromEnv("provider") && <p className="hint" style={{ marginTop: 10 }}>The provider is set by an environment variable on the server, so it cannot be changed here.</p>}
      </div>

      {prov && (
        <div className="card">
          <h3>2 · Connect it</h3>
          <div className="form-grid">
            <label className="field">{HELP[f.provider].key}
              <input type="password" autoComplete="off" value={key} disabled={fromEnv("api_key")} onChange={(e) => { setKey(e.target.value); setSaved(""); }} placeholder={fromEnv("api_key") ? "set by an environment variable" : s.has_key && s.provider === f.provider ? `saved ${s.key_hint}  (type to replace)` : "paste the key"} />
              <span className="hint">{HELP[f.provider].where} The key is stored on the machine that runs devmem and is never shown again.</span>
            </label>
            <label className="field">Model
              <input value={f.model} onChange={(e) => set("model", e.target.value)} placeholder={prov.model} disabled={fromEnv("model")} />
              <span className="hint">A small, fast model is enough: it writes one-sentence descriptions and short cited answers.</span>
            </label>
            {f.provider === "openai" && (
              <label className="field wide">Server address
                <input value={f.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder="https://api.openai.com/v1" disabled={fromEnv("base_url")} />
                <span className="hint">{HELP.openai.base}. Any server that speaks the OpenAI chat format works.</span>
              </label>
            )}
            {f.provider === "vertex" && (
              <>
                <label className="field">Google Cloud project<input value={f.project} onChange={(e) => set("project", e.target.value)} placeholder="my-gcp-project (with gcloud auth application-default login)" /></label>
                <label className="field">Region<input value={f.location} onChange={(e) => set("location", e.target.value)} placeholder="us-central1" /></label>
              </>
            )}
            {f.provider !== "anthropic" && (
              <label className="field wide">Embedding model for search by meaning (optional)
                <input value={f.embed_model} onChange={(e) => set("embed_model", e.target.value)} placeholder={prov.embed_model || (f.provider === "openai" ? "text-embedding-3-small" : "leave empty to skip")} disabled={fromEnv("embed_model")} />
              </label>
            )}
          </div>
          <div className="row" style={{ marginTop: 18 }}>
            <button className="primary" disabled={!!busy} onClick={() => save(true)}><Icon name="plug" />{busy === "test" ? "Testing…" : "Save and test"}</button>
            <button disabled={!!busy} onClick={() => save(false)}>{busy === "save" ? "Saving…" : "Save"}</button>
            {(s.configured || s.has_key) && !fromEnv("provider") && <button className="ghost danger" disabled={!!busy} onClick={turnOff}>Turn AI off and forget the key</button>}
            {saved && !test && <span className="muted">{saved}</span>}
          </div>
          {test && (test.ok
            ? <div className="ok">Connected. {test.model} answered in {test.ms} ms.</div>
            : <div className="err" role="alert">The model did not answer: {test.error}</div>)}
        </div>
      )}

      <div className="card">
        <h3>Search by meaning without any API</h3>
        <label className="row"><input type="checkbox" checked={!!f.local_embed} disabled={fromEnv("local_embed")} onChange={(e) => set("local_embed", e.target.checked)} />Use a small local model for search by meaning (downloads about 30 MB once, then runs offline on the CPU)</label>
        <p className="hint" style={{ margin: "8px 0 12px" }}>With it, “make flaky calls reliable” also finds <code>retry</code>. Needs <code>pip install model2vec</code> on the machine that runs devmem. Nothing leaves that machine.</p>
        <div className="row">
          <button disabled={!!busy} onClick={() => save(false)}>Save</button>
          {test?.local_embed && test.local_embed !== "off" && <span className={test.local_embed === "ready" ? "muted" : "hint"}>Local model: {test.local_embed}</span>}
        </div>
      </div>

      <div className="card">
        <h3>What is sent to the model</h3>
        <ul className="plain">
          <li><b>Nothing</b> while AI is off, and nothing during indexing either way: the index is built by parsers.</li>
          <li><b>Written answers:</b> your question and the few code snippets and doc sections that matched it.</li>
          <li><b>Descriptions:</b> the first lines of each function you select, with the names of what calls it and what it calls.</li>
          <li><b>Search by meaning with a provider's embedding model:</b> the question and the name, signature and docstring of each candidate.</li>
        </ul>
      </div>
    </>
  );
}
