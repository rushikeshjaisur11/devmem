import { retry, backoff } from "./retry";
import * as util from "./util";
import fmt from "./format";

export class Client {
  constructor(private base: string) {}
  /** Fetch JSON with retries. */
  async get(path: string) {
    return retry(() => this.raw(path));
  }
  raw(path: string) {
    return fetch(this.base + util.join(path)).then((r) => r.json());
  }
  delay(n: number) { return backoff(n); }
}

export function describeClient(c: Client) {
  return fmt(c);
}
