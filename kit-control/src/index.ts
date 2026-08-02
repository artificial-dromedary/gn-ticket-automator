/**
 * Worker entry point.
 *
 * Every route except `GET /` requires the bearer token. `GET /` serves the PWA,
 * which prompts for that token itself and keeps it in localStorage.
 */

import { lockAll, lockChild, unlockChild, type ActionResult } from "./actions.js";
import { childBySlug } from "./config.js";
import { KitError, asKitError } from "./errors.js";
import { PWA_HTML, MANIFEST_JSON } from "./pwa.js";
import { QustodioClient, kvTokenStore } from "./qustodio.js";
import { getStatus } from "./status.js";
import { kvStore } from "./store.js";

export interface Env {
  KIT: KVNamespace;
  QUSTODIO_EMAIL: string;
  QUSTODIO_PASSWORD: string;
  QUSTODIO_CLIENT_ID: string;
  QUSTODIO_CLIENT_SECRET: string;
  KIT_CONTROL_TOKEN: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "GET" && path === "/") {
      return html(PWA_HTML);
    }
    if (request.method === "GET" && path === "/manifest.webmanifest") {
      return new Response(MANIFEST_JSON, {
        headers: { "Content-Type": "application/manifest+json; charset=utf-8" },
      });
    }

    try {
      await requireBearer(request, env);
    } catch (e) {
      return errorResponse(asKitError(e));
    }

    try {
      return await route(request, env, path, url);
    } catch (e) {
      const error = asKitError(e);
      console.log("request failed", { path, code: error.code, detail: error.detail });
      return errorResponse(error);
    }
  },
} satisfies ExportedHandler<Env>;

async function route(request: Request, env: Env, path: string, url: URL): Promise<Response> {
  const client = makeClient(env);
  const store = kvStore(env.KIT);

  if (request.method === "GET" && path === "/api/health") {
    await client.ping();
    return json({ ok: true, status: "ok" });
  }

  if (request.method === "GET" && path === "/api/status") {
    const fresh = url.searchParams.get("fresh") === "1";
    const payload = await getStatus(client, store, {
      fresh,
      log: (message, data) => console.log(message, data ?? ""),
    });
    return json(payload);
  }

  if (request.method === "POST" && path === "/api/lock-all") {
    const result = await lockAll(client, store);
    return json(result, result.ok ? 200 : 207);
  }

  const lockMatch = /^\/api\/(lock|unlock)\/([a-z-]+)$/.exec(path);
  if (request.method === "POST" && lockMatch) {
    const [, verb, slug] = lockMatch;
    const child = childBySlug(slug ?? "");
    if (!child) {
      throw new KitError("unknown_child", `No child with slug "${slug}"`);
    }

    const result: ActionResult =
      verb === "lock"
        ? await lockChild(client, store, child)
        : await unlockChild(client, store, child);

    // already_locked is not an error, but it is a refusal to write, so it gets
    // a 409 to distinguish it from a lock that just happened.
    return json(result, result.status === "already_locked" ? 409 : 200);
  }

  return json({ error: "not_found", detail: `${request.method} ${path}` }, 404);
}

function makeClient(env: Env): QustodioClient {
  for (const name of [
    "QUSTODIO_EMAIL",
    "QUSTODIO_PASSWORD",
    "QUSTODIO_CLIENT_ID",
    "QUSTODIO_CLIENT_SECRET",
  ] as const) {
    if (!env[name]) {
      throw new KitError("auth_failed", `${name} is not set. Run: wrangler secret put ${name}`);
    }
  }

  return new QustodioClient(
    {
      email: env.QUSTODIO_EMAIL,
      password: env.QUSTODIO_PASSWORD,
      clientId: env.QUSTODIO_CLIENT_ID,
      clientSecret: env.QUSTODIO_CLIENT_SECRET,
    },
    kvTokenStore(env.KIT),
    { log: (message, data) => console.log(message, data ?? "") },
  );
}

async function requireBearer(request: Request, env: Env): Promise<void> {
  if (!env.KIT_CONTROL_TOKEN) {
    throw new KitError(
      "unauthorized",
      "KIT_CONTROL_TOKEN is not set. Run: wrangler secret put KIT_CONTROL_TOKEN",
    );
  }

  const header = request.headers.get("Authorization") ?? "";
  const presented = header.startsWith("Bearer ") ? header.slice("Bearer ".length).trim() : "";

  if (!(await constantTimeEquals(presented, env.KIT_CONTROL_TOKEN))) {
    throw new KitError("unauthorized", "Missing or invalid bearer token");
  }
}

/**
 * Compare via SHA-256 digests, so neither the comparison nor the length of the
 * presented value leaks anything through timing.
 */
async function constantTimeEquals(a: string, b: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [da, db] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(a)),
    crypto.subtle.digest("SHA-256", encoder.encode(b)),
  ]);

  const x = new Uint8Array(da);
  const y = new Uint8Array(db);
  let diff = 0;
  for (let i = 0; i < x.length; i += 1) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function html(body: string): Response {
  return new Response(body, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-cache",
    },
  });
}

function errorResponse(error: KitError): Response {
  return json({ ...error.toJSON(), ok: false, spoken: spokenFor(error) }, error.status);
}

/** Failures must say so plainly when Siri reads them out. */
function spokenFor(error: KitError): string {
  switch (error.code) {
    case "unauthorized":
      return "Kit Control rejected the token.";
    case "unknown_child":
      return "I don't know that name.";
    case "lock_disabled":
      return "That child can't be locked yet. Check the app.";
    case "auth_failed":
      return "Kit Control couldn't sign in to Qustodio. Check the app.";
    case "verify_failed":
      return "The change may not have applied. Check the app.";
    case "api_changed":
      return "Qustodio returned something unexpected. Check the app.";
  }
}
