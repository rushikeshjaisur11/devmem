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
