import { describe, it, expect } from "vitest";
import { Client } from "../src/client";
import { retry } from "../src/retry";

describe("Client", () => {
  it("builds delays", () => {
    expect(new Client("x").delay(1)).toBe(100);
  });
  it.skip("fetches", async () => {
    await new Client("x").get("a");
  });
});
describe("retry", () => {
  it("retries", async () => { await retry(async () => 1); });
});
