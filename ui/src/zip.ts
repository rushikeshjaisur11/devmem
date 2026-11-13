/* Pack a folder picked in the browser into one .zip for upload. Entries are stored uncompressed: source code uploads fast
   enough as it is, and a store-only writer needs no library. Dependency and VCS folders are left out, as the server would
   drop them anyway. */

export const SKIP_DIRS = new Set([".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", ".idea"]);
const MAX_FILE = 5 * 1024 * 1024;      // the indexer ignores files over 1 MB; anything this large is data, not code
const MAX_ENTRIES = 65000;             // classic zip limits (no zip64)
const MAX_TOTAL = 3.5 * 1024 ** 3;

const TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
function crc32(b: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < b.length; i++) c = TABLE[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

export interface Picked { path: string; file: File }

/** Files worth sending, with paths relative to the picked folder's parent (so the folder name stays as the top level). */
export function pickable(files: Picked[]): { keep: Picked[]; skipped: number } {
  const keep = files.filter(({ path, file }) => !path.split("/").some((p) => SKIP_DIRS.has(p)) && file.size <= MAX_FILE);
  return { keep, skipped: files.length - keep.length };
}

export async function zipFiles(files: Picked[], onProgress: (fraction: number) => void): Promise<Blob> {
  if (files.length > MAX_ENTRIES) throw new Error(`This folder has ${files.length.toLocaleString()} files; the browser upload handles up to ${MAX_ENTRIES.toLocaleString()}. Zip it yourself and upload the .zip, or use a Git URL.`);
  const total = files.reduce((n, f) => n + f.file.size, 0);
  if (total > MAX_TOTAL) throw new Error("This folder is larger than 3.5 GB. Zip only the source code, or use a Git URL.");
  const enc = new TextEncoder();
  const parts: BlobPart[] = [];
  const central: BlobPart[] = [];
  let offset = 0, done = 0, size = 0;
  for (const { path, file } of files) {
    const data = new Uint8Array(await file.arrayBuffer());
    const name = enc.encode(path);
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);   // 0x0800: names are UTF-8
    local.setUint32(14, crc, true); local.setUint32(18, data.length, true); local.setUint32(22, data.length, true); local.setUint16(26, name.length, true);
    const cen = new DataView(new ArrayBuffer(46));
    cen.setUint32(0, 0x02014b50, true); cen.setUint16(4, 20, true); cen.setUint16(6, 20, true); cen.setUint16(8, 0x0800, true);
    cen.setUint32(16, crc, true); cen.setUint32(20, data.length, true); cen.setUint32(24, data.length, true); cen.setUint16(28, name.length, true);
    cen.setUint32(42, offset, true);
    parts.push(local.buffer, name as BlobPart, data as BlobPart);
    central.push(cen.buffer, name as BlobPart);
    size += 46 + name.length;
    offset += 30 + name.length + data.length;
    done += file.size;
    onProgress(total ? done / total : 1);
  }
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, size, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end.buffer], { type: "application/zip" });
}

/** Everything under a dropped folder (drag and drop gives directory entries, not a flat file list). */
export async function readDropped(items: DataTransferItemList): Promise<Picked[]> {
  const out: Picked[] = [];
  const walk = async (entry: any, prefix: string): Promise<void> => {
    if (entry.isFile) {
      const file: File = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ path: prefix + entry.name, file });
    } else if (entry.isDirectory && !SKIP_DIRS.has(entry.name)) {
      const reader = entry.createReader();
      for (;;) {   // readEntries returns the directory in batches
        const batch: any[] = await new Promise((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        for (const e of batch) await walk(e, prefix + entry.name + "/");
      }
    }
  };
  const entries = Array.from(items).map((i) => (i as any).webkitGetAsEntry?.()).filter(Boolean);
  for (const e of entries) await walk(e, "");
  return out;
}
