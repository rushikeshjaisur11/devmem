export type Json = any;

async function req(method: string, url: string, body?: Json): Promise<Json> {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-Devmem": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  let data: Json = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (r.status === 401 && url !== "/api/login") window.dispatchEvent(new Event("devmem-signin"));   // deployed server: show the sign-in page
  if (!r.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : JSON.stringify(d ?? data ?? r.statusText));
  }
  return data;
}

export const api = {
  get: (url: string) => req("GET", url),
  post: (url: string, body: Json = {}) => req("POST", url, body),
  put: (url: string, body: Json) => req("PUT", url, body),
  del: (url: string) => req("DELETE", url),
};

export const q = (o: Record<string, string | number | undefined | null>) =>
  Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join("&");

/** POST a file as the request body, reporting progress (fetch cannot report upload progress). */
export function upload(url: string, body: Blob, onProgress: (fraction: number) => void): Promise<Json> {
  // the bytes go up in the request; unpacking and indexing continue as a job on the server (see job / waitJob)
  return uploadOnly(url + (url.includes("?") ? "&" : "?") + "background=1", body, onProgress).then(waitJob);
}

function uploadOnly(url: string, body: Blob, onProgress: (fraction: number) => void): Promise<Json> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", url);
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.setRequestHeader("X-Devmem", "1");
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onerror = () => reject(new Error("The upload was interrupted. Check the connection and try again."));
    x.onload = () => {
      let data: Json = null;
      try { data = x.responseText ? JSON.parse(x.responseText) : null; } catch { data = x.responseText; }
      if (x.status === 401) window.dispatchEvent(new Event("devmem-signin"));
      if (x.status >= 200 && x.status < 300) return resolve(data);
      const d = data && data.detail;
      reject(new Error(typeof d === "string" ? d : x.status === 413 ? "The upload is larger than this server accepts." : JSON.stringify(d ?? data ?? x.statusText)));
    };
    x.send(body);
  });
}

/** POST to an endpoint that can run as a background job (`?background=1`): the server answers at once with a job id and
 * keeps working even if this page is left or a proxy would have timed out. Polls until the job finishes and returns its
 * result, or throws its error. Endpoints that finish immediately return their result directly. */
export async function job(url: string, body: Json = {}): Promise<Json> {
  const r = await req("POST", url + (url.includes("?") ? "&" : "?") + "background=1", body);
  return waitJob(r);
}

export async function waitJob(r: Json): Promise<Json> {
  if (!r || typeof r !== "object" || !r.job) return r;
  for (let wait = 600; ; wait = Math.min(wait * 1.4, 3000)) {
    await new Promise((res) => setTimeout(res, wait));
    let j: Json;
    try { j = await req("GET", `/api/jobs/${r.job}`); }
    catch (e: any) { throw new Error(/unknown job/.test(e.message) ? "The server restarted while this was running. Run it again." : e.message); }
    if (j.state === "done") return j.result;
    if (j.state === "failed") throw new Error(j.error || "failed");
  }
}
