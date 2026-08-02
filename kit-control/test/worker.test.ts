/**
 * Route-level tests: bearer auth, error shapes, and the PWA response.
 *
 * Only paths that do not go through the 3-second verify wait are exercised
 * here; lock and unlock behaviour is covered directly in actions.test.ts.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import worker, { type Env } from "../src/index.js";
import { FakeQustodio } from "./fake-qustodio.js";

const TOKEN = "test-token-do-not-use-in-production";

/** Just enough KVNamespace for the Worker's use of it. */
function fakeKv(): KVNamespace {
  const map = new Map<string, string>();
  return {
    async get(key: string, type?: string) {
      const raw = map.get(key);
      if (raw === undefined) return null;
      return type === "json" ? JSON.parse(raw) : raw;
    },
    async put(key: string, value: string) {
      map.set(key, value);
    },
    async delete(key: string) {
      map.delete(key);
    },
  } as unknown as KVNamespace;
}

function makeEnv(): Env {
  return {
    KIT: fakeKv(),
    QUSTODIO_EMAIL: "parent@example.com",
    QUSTODIO_PASSWORD: "hunter2",
    QUSTODIO_CLIENT_ID: "cid",
    QUSTODIO_CLIENT_SECRET: "secret",
    KIT_CONTROL_TOKEN: TOKEN,
  };
}

const call = (path: string, init: RequestInit & { token?: string | null } = {}) => {
  const headers = new Headers(init.headers);
  if (init.token !== null) headers.set("Authorization", `Bearer ${init.token ?? TOKEN}`);
  return worker.fetch(new Request(`https://kit.example.com${path}`, { ...init, headers }), env);
};

let fake: FakeQustodio;
let env: Env;
let realFetch: typeof fetch;

beforeEach(() => {
  fake = new FakeQustodio();
  env = makeEnv();
  realFetch = globalThis.fetch;
  globalThis.fetch = fake.fetch;
});

afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("GET /", () => {
  it("serves the PWA without auth", async () => {
    const response = await worker.fetch(new Request("https://kit.example.com/"), env);
    const body = await response.text();

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toMatch(/text\/html/);
    expect(body).toContain("Kit Control");
    expect(body).toContain("apple-mobile-web-app-capable");
  });

  it("makes no external requests — everything is inline", async () => {
    const body = await (await worker.fetch(new Request("https://kit.example.com/"), env)).text();
    // Only same-origin hrefs and data: URIs may appear.
    const external = body.match(/(?:src|href)\s*=\s*"(?!\/|data:|#)[^"]*"/g) ?? [];
    expect(external).toEqual([]);
  });

  it("serves the manifest", async () => {
    const response = await worker.fetch(
      new Request("https://kit.example.com/manifest.webmanifest"),
      env,
    );
    expect(response.status).toBe(200);
    expect(JSON.parse(await response.text()).name).toBe("Kit Control");
  });
});

describe("auth", () => {
  it("rejects a missing token", async () => {
    const response = await call("/api/status", { token: null });
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({ error: "unauthorized" });
  });

  it("rejects a wrong token", async () => {
    const response = await call("/api/status", { token: "nope" });
    expect(response.status).toBe(401);
  });

  it("rejects a token that is a prefix of the real one", async () => {
    const response = await call("/api/status", { token: TOKEN.slice(0, -1) });
    expect(response.status).toBe(401);
  });

  it("accepts the right token", async () => {
    expect((await call("/api/status")).status).toBe(200);
  });
});

describe("routes", () => {
  it("returns status for every child", async () => {
    const body = (await (await call("/api/status")).json()) as {
      fetchedAt: string;
      children: Array<Record<string, unknown>>;
    };

    expect(body.children).toHaveLength(3);
    expect(body.fetchedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(body.children.map((c) => c.slug)).toEqual(["taysha", "selah", "coen"]);
  });

  it("never leaks a child's location through the status route", async () => {
    const text = await (await call("/api/status")).text();
    expect(text).not.toMatch(/latitude|longitude|address|Somewhere St/i);
  });

  it("answers health with a real call against the API", async () => {
    const response = await call("/api/health");
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ ok: true, status: "ok" });
  });

  it("refuses to lock a child whose prerequisite is outstanding", async () => {
    const response = await call("/api/lock/taysha", { method: "POST" });

    expect(response.status).toBe(412);
    expect(await response.json()).toMatchObject({
      error: "lock_disabled",
      ok: false,
      spoken: expect.stringMatching(/can't be locked yet/),
    });
    expect(fake.writes()).toHaveLength(0);
  });

  it("returns already_unlocked without deleting anything", async () => {
    const response = await call("/api/unlock/coen", { method: "POST" });

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ status: "already_unlocked", ok: true });
    expect(fake.deletes()).toHaveLength(0);
  });

  it("404s an unknown child", async () => {
    const response = await call("/api/lock/rover", { method: "POST" });
    expect(response.status).toBe(404);
    expect(await response.json()).toMatchObject({ error: "unknown_child" });
  });

  it("404s an unknown path", async () => {
    expect((await call("/api/nonsense")).status).toBe(404);
  });

  it("gives every error a spoken sentence that admits the failure", async () => {
    const body = (await (await call("/api/lock/taysha", { method: "POST" })).json()) as {
      spoken: string;
    };
    expect(body.spoken).toMatch(/Check the app/);
  });
});
