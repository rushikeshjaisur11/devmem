/** Retry a function a few times. */
export async function retry<T>(fn: () => Promise<T>, attempts = 3): Promise<T> {
  let last: unknown;
  for (let i = 0; i < attempts; i++) {
    try { return await fn(); } catch (e) { last = e; }
  }
  throw last;
}

export const backoff = (n: number) => Math.min(1000, 2 ** n * 50);
