import { useEffect, useState } from "react";

export interface Route { page: string; params: Record<string, string> }

export function parse(): Route {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [page, qs = ""] = h.split("?");
  const params: Record<string, string> = {};
  new URLSearchParams(qs).forEach((v, k) => (params[k] = v));
  return { page: page || "repos", params };
}

export function nav(page: string, params: Record<string, string | undefined> = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]).toString();
  window.location.hash = `/${page}${qs ? "?" + qs : ""}`;
}

export function useRoute(): Route {
  const [r, setR] = useState(parse());
  useEffect(() => {
    const f = () => setR(parse());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return r;
}
