import { useEffect, useRef, useState } from "react";
import { upload } from "./api";
import { fmtBytes, Icon } from "./components";
import { Picked, pickable, readDropped, zipFiles } from "./zip";

interface Chosen { label: string; detail: string; name: string; archive?: File; files?: Picked[] }


export interface UploadText { title: string; note: any; nameLabel: string; action: string; working: string }
const REPO_TEXT: UploadText = { title: "Drop a project folder or a .zip here", note: <>Dependencies and <code>.git</code> are left out</>, nameLabel: "Repo name", action: "Upload and index", working: "Uploaded. Indexing the code; a large repo takes a minute or two." };

/** Upload a folder or an archive: how things get onto the machine that runs devmem when it is not the one you are sitting at. */
export function UploadBox({ maxMb, endpoint, onDone, onError, text = REPO_TEXT }: { maxMb: number; endpoint: (name: string, filename: string) => string; onDone: (r: any) => void; onError: (m: string) => void; text?: UploadText }) {
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const [name, setName] = useState("");
  const [over, setOver] = useState(false);
  const [stage, setStage] = useState<"" | "pack" | "send" | "index">("");
  const [frac, setFrac] = useState(0);
  const folderRef = useRef<HTMLInputElement>(null);
  const zipRef = useRef<HTMLInputElement>(null);
  useEffect(() => { folderRef.current?.setAttribute("webkitdirectory", ""); }, []);

  const takeFolder = (all: Picked[]) => {
    if (!all.length) return;
    const { keep, skipped } = pickable(all);
    if (!keep.length) return onError("That folder has no files to index (only dependencies or very large files).");
    const top = keep[0].path.split("/")[0];
    const size = keep.reduce((n, f) => n + f.file.size, 0);
    setChosen({ label: top, name: top, files: keep, detail: `${keep.length.toLocaleString()} files, ${fmtBytes(size)}${skipped ? ` (${skipped.toLocaleString()} left out: dependencies, version control, files over 5 MB)` : ""}` });
    setName(top); onError("");
  };
  const takeArchive = (f: File) => {
    if (!/\.(zip|tar|tgz|tar\.gz|tar\.bz2|tar\.xz)$/i.test(f.name)) return onError("Choose a .zip or .tar.gz file, or pick the folder itself.");
    const base = f.name.replace(/\.(zip|tar\.gz|tgz|tar\.bz2|tar\.xz|tar)$/i, "");
    setChosen({ label: f.name, name: base, archive: f, detail: fmtBytes(f.size) }); setName(base); onError("");
  };
  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault(); setOver(false);
    const one = e.dataTransfer.files.length === 1 ? e.dataTransfer.files[0] : null;
    if (one && /\.(zip|tar|tgz|gz|bz2|xz)$/i.test(one.name)) return takeArchive(one);
    try { takeFolder(await readDropped(e.dataTransfer.items)); } catch { onError("Could not read the dropped folder. Use Choose folder instead."); }
  };
  const send = async () => {
    if (!chosen) return;
    onError("");
    try {
      let body: Blob = chosen.archive as Blob;
      let filename = chosen.archive?.name || `${chosen.name}.zip`;
      if (chosen.files) { setStage("pack"); setFrac(0); body = await zipFiles(chosen.files, setFrac); filename = `${chosen.name}.zip`; }
      if (body.size > maxMb * 1024 * 1024) throw new Error(`This is ${fmtBytes(body.size)}; the server accepts up to ${maxMb} MB. Upload only the source code, or use a Git URL.`);
      setStage("send"); setFrac(0);
      const r = await upload(endpoint(name.trim(), filename), body, (f) => { setFrac(f); if (f >= 1) setStage("index"); });
      setChosen(null); setName(""); onDone(r);
    } catch (e: any) { onError(e.message); }
    setStage("");
  };
  const busy = stage !== "";
  return (
    <div>
      <div className={`drop ${over ? "over" : ""} ${chosen ? "has" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
        <input ref={folderRef} type="file" multiple hidden onChange={(e) => { takeFolder(Array.from(e.target.files || []).map((f) => ({ path: (f as any).webkitRelativePath || f.name, file: f }))); e.target.value = ""; }} />
        <input ref={zipRef} type="file" hidden accept=".zip,.tar,.tgz,.gz,.bz2,.xz" onChange={(e) => { const f = e.target.files?.[0]; if (f) takeArchive(f); e.target.value = ""; }} />
        {chosen ? (
          <div className="drop-file">
            <span className="drop-ico"><Icon name={chosen.files ? "folder" : "file"} /></span>
            <div className="grow"><b>{chosen.label}</b><div className="muted">{chosen.detail}</div></div>
            <button className="ghost" disabled={busy} onClick={() => setChosen(null)}>Choose another</button>
          </div>
        ) : (
          <>
            <span className="drop-ico"><Icon name="upload" /></span>
            <div className="drop-title">{text.title}</div>
            <div className="muted">or pick it from your computer. {text.note}; up to {maxMb} MB.</div>
            <div className="row" style={{ justifyContent: "center", marginTop: 14 }}>
              <button onClick={() => folderRef.current?.click()}><Icon name="folder" />Choose folder</button>
              <button onClick={() => zipRef.current?.click()}><Icon name="file" />Choose .zip or .tar.gz</button>
            </div>
          </>
        )}
      </div>
      {chosen && (
        <div className="row" style={{ marginTop: 12 }}>
          <label className="field inline grow">{text.nameLabel}<input value={name} onChange={(e) => setName(e.target.value)} aria-label={text.nameLabel} disabled={busy} /></label>
          <button className="primary" disabled={busy || !name.trim()} onClick={send}><Icon name="upload" />{stage === "pack" ? "Packing…" : stage === "send" ? "Uploading…" : stage === "index" ? "Working…" : text.action}</button>
        </div>
      )}
      {busy && (
        <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(frac * 100)} aria-label="Upload progress">
          <div className={stage === "index" ? "bar busy" : "bar"} style={{ width: stage === "index" ? "100%" : `${Math.round(frac * 100)}%` }} />
          <span>{stage === "pack" ? `Packing the folder, ${Math.round(frac * 100)}%` : stage === "send" ? `Uploading, ${Math.round(frac * 100)}%` : text.working}</span>
        </div>
      )}
    </div>
  );
}

