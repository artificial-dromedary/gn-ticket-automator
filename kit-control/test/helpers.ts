import { QustodioClient, type CachedToken, type TokenStore } from "../src/qustodio.js";
import type { FakeQustodio } from "./fake-qustodio.js";

/** Saturday 2026-08-01, 22:10 Edmonton local. */
export const SATURDAY_2210 = new Date("2026-08-02T04:10:00Z");

export function memoryTokenStore(): TokenStore {
  let token: CachedToken | null = null;
  return {
    async get() {
      return token;
    },
    async set(value) {
      token = value;
    },
    async clear() {
      token = null;
    },
  };
}

/** Client wired to the fake, with a frozen clock and no real waiting. */
export function makeClient(fake: FakeQustodio, now: Date = SATURDAY_2210): QustodioClient {
  return new QustodioClient(
    { email: "parent@example.com", password: "hunter2", clientId: "cid", clientSecret: "secret" },
    memoryTokenStore(),
    {
      fetch: fake.fetch,
      now: () => now,
      sleep: async () => {},
      log: () => {},
    },
  );
}
