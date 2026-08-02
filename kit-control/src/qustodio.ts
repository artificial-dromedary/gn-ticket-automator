/**
 * Qustodio API client.
 *
 * Deliberately knows nothing about the Worker's routes, so it can be replaced
 * wholesale when Qustodio changes something. There is no official API; this
 * talks to the private one behind the parent web portal.
 *
 * Two safety rails live in here rather than in the callers, because the caller
 * is the thing most likely to be wrong:
 *  - a schedule is never DELETEd unless the object just read has `overrides === true`
 *  - a schedule is never POSTed with `overrides` anything but true
 */

import {
  API_BASE,
  ACCOUNT_ID,
  ACCOUNT_UID,
  AUTH_BASE,
  ORIGIN,
  REDIRECT_URI,
  USER_AGENT,
} from "./config.js";
import { KitError } from "./errors.js";
import type { Weekday } from "./time.js";

/** Thrown when a caller tries to delete something that is not an override. */
export class ScheduleGuardError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ScheduleGuardError";
  }
}

export interface Profile {
  id: number;
  uid: string;
  name: string;
  /** A routine uid, or null when no routine is in effect. Display only. */
  active_routine: string | null;
}

export interface Routine {
  uid: string;
  name: string;
}

export interface Schedule {
  uid: string;
  duration_minutes: number;
  weekdays: string[];
  start_time: string;
  from_date: string;
  to_date: string | null;
  /**
   * false: the child's permanent recurring schedule.
   * true: a temporary manual switch.
   *
   * This flag is the only reliable signal that a child is manually locked, and
   * the only thing that makes a schedule safe to delete.
   */
  overrides: boolean;
  enabled: boolean;
}

export interface CachedToken {
  access_token: string;
  refresh_token: string | null;
  /** Epoch ms. */
  expires_at: number;
}

export interface TokenStore {
  get(): Promise<CachedToken | null>;
  set(token: CachedToken): Promise<void>;
  clear(): Promise<void>;
}

export interface Credentials {
  email: string;
  password: string;
  clientId: string;
  clientSecret: string;
}

export interface ClientOptions {
  fetch?: typeof fetch;
  now?: () => Date;
  log?: (message: string, data?: unknown) => void;
  /** Retry/verify sleep. Overridden in tests so they do not actually wait. */
  sleep?: (ms: number) => Promise<void>;
}

/** Captured from the web SPA. Re-encoded rather than pasted so it is readable. */
const LOGIN_DETAILS = {
  source_platform: "Web",
  source_details: "PAR-182.38.0-66-g5fca83cc",
  source_os_version: "151.0.0.0",
  source_touchpoint: "Parent Device",
};

const LOGIN_CONF = "eyJzaG93QmFja0J1dHRvbiI6ZmFsc2V9";

/** Refresh this long before the token actually expires. */
const TOKEN_SKEW_MS = 60_000;

function base64Utf8(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/**
 * Recursively pull the first string containing `authorization_code=` out of a
 * JSON value. The do_login response shape was never captured, so the field
 * name is unknown; this finds it wherever it lives.
 */
export function findAuthorizationCode(value: unknown, depth = 0): string | null {
  if (depth > 8) return null;

  if (typeof value === "string") {
    if (!value.includes("authorization_code=")) return null;
    // Works whether the value is a full URL or a bare query fragment.
    const match = /[?&#]authorization_code=([^&#\s"']+)/.exec(value);
    if (match?.[1]) return decodeURIComponent(match[1]);
    const loose = /authorization_code=([^&#\s"']+)/.exec(value);
    return loose?.[1] ? decodeURIComponent(loose[1]) : null;
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findAuthorizationCode(item, depth + 1);
      if (found) return found;
    }
    return null;
  }

  if (value && typeof value === "object") {
    for (const item of Object.values(value)) {
      const found = findAuthorizationCode(item, depth + 1);
      if (found) return found;
    }
  }

  return null;
}

/**
 * Keys that can carry a child's whereabouts. The profiles response is the only
 * one known to include them, and this client narrows that response to four
 * fields anyway — but anything else parsed from Qustodio goes through here so
 * a location block cannot reach a log, KV, or an HTTP response.
 */
const LOCATION_KEYS = new Set([
  "location",
  "locations",
  "address",
  "addresses",
  "latitude",
  "longitude",
  "lat",
  "lon",
  "lng",
  "accuracy",
  "coordinates",
  "geo",
  "place",
  "places",
  "last_location",
  "last_seen_location",
]);

export function stripLocation<T>(value: T, depth = 0): T {
  if (depth > 12 || value === null || typeof value !== "object") return value;

  if (Array.isArray(value)) {
    return value.map((item) => stripLocation(item, depth + 1)) as unknown as T;
  }

  const out: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (LOCATION_KEYS.has(key.toLowerCase())) continue;
    out[key] = stripLocation(item, depth + 1);
  }
  return out as unknown as T;
}

/** Responses come back either bare or wrapped in `{ total_count, items_list }`. */
function itemsOf(payload: unknown, what: string): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object") {
    const list = (payload as Record<string, unknown>).items_list;
    if (Array.isArray(list)) return list;
    const results = (payload as Record<string, unknown>).results;
    if (Array.isArray(results)) return results;
  }
  throw new KitError("api_changed", `${what} response was not a list`);
}

export class QustodioClient {
  readonly #credentials: Credentials;
  readonly #tokens: TokenStore;
  readonly #fetch: typeof fetch;
  readonly #now: () => Date;
  readonly #log: (message: string, data?: unknown) => void;
  readonly #sleep: (ms: number) => Promise<void>;

  constructor(credentials: Credentials, tokens: TokenStore, options: ClientOptions = {}) {
    this.#credentials = credentials;
    this.#tokens = tokens;
    this.#fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.#now = options.now ?? (() => new Date());
    this.#log = options.log ?? ((message, data) => console.log(message, data ?? ""));
    this.#sleep =
      options.sleep ?? ((ms: number) => new Promise((resolve) => setTimeout(resolve, ms)));
  }

  sleep(ms: number): Promise<void> {
    return this.#sleep(ms);
  }

  now(): Date {
    return this.#now();
  }

  // ---------------------------------------------------------------- auth

  async #login(): Promise<CachedToken> {
    const state = crypto.randomUUID();

    const loginBody = new URLSearchParams({
      email: this.#credentials.email,
      password: this.#credentials.password,
      clientId: this.#credentials.clientId,
      state,
      redirectUri: REDIRECT_URI,
      details: base64Utf8(JSON.stringify(LOGIN_DETAILS)),
      conf: LOGIN_CONF,
      allowAffiliates: "true",
      code_challenge: "",
      code_challenge_method: "",
    });

    const loginResponse = await this.#fetch(`${AUTH_BASE}/en/sso/do_login/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Origin: ORIGIN,
        "User-Agent": USER_AGENT,
        Accept: "application/json",
      },
      body: loginBody.toString(),
    });

    const loginText = await loginResponse.text();
    if (!loginResponse.ok) {
      throw new KitError(
        "auth_failed",
        `do_login returned ${loginResponse.status}. Qustodio rejected credentials or the login form changed.`,
      );
    }

    let loginJson: unknown;
    try {
      loginJson = JSON.parse(loginText);
    } catch {
      this.#log("do_login returned a non-JSON body", { body: loginText.slice(0, 2000) });
      throw new KitError("api_changed", "do_login response was not JSON");
    }

    // Section 12: this response shape was never captured. Log its keys every
    // time (cheap, no secrets) and the whole body only when parsing fails.
    this.#log("do_login response keys", Object.keys(loginJson as Record<string, unknown>));

    const code = findAuthorizationCode(loginJson);
    if (!code) {
      this.#log("do_login response carried no authorization_code", {
        body: loginText.slice(0, 2000),
      });
      throw new KitError(
        "api_changed",
        "do_login succeeded but no field carried an authorization_code",
      );
    }

    return await this.#exchange({
      grant_type: "authorization_code",
      code,
      redirect_uri: REDIRECT_URI,
    });
  }

  async #refresh(refreshToken: string): Promise<CachedToken> {
    return await this.#exchange({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
    });
  }

  async #exchange(fields: Record<string, string>): Promise<CachedToken> {
    const body = new URLSearchParams({
      client_id: this.#credentials.clientId,
      client_secret: this.#credentials.clientSecret,
      ...fields,
    });

    const response = await this.#fetch(`${API_BASE}/v1/oauth2/access_token`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Origin: ORIGIN,
        "User-Agent": USER_AGENT,
        Accept: "application/json",
      },
      body: body.toString(),
    });

    const text = await response.text();
    if (!response.ok) {
      throw new KitError(
        "auth_failed",
        `access_token (${fields.grant_type}) returned ${response.status}`,
      );
    }

    let json: Record<string, unknown>;
    try {
      json = JSON.parse(text) as Record<string, unknown>;
    } catch {
      throw new KitError("api_changed", "access_token response was not JSON");
    }

    // Section 12: whether a refresh_token is issued is unconfirmed. Log which
    // fields came back — names only, never values.
    this.#log("access_token response keys", Object.keys(json));

    const accessToken = json.access_token;
    if (typeof accessToken !== "string" || accessToken.length === 0) {
      throw new KitError("api_changed", "access_token response had no access_token");
    }

    const expiresIn = typeof json.expires_in === "number" ? json.expires_in : 3600;
    const refreshToken = typeof json.refresh_token === "string" ? json.refresh_token : null;

    return {
      access_token: accessToken,
      refresh_token: refreshToken,
      expires_at: this.#now().getTime() + expiresIn * 1000,
    };
  }

  /** Cached token, refreshed or re-logged-in as needed. */
  async #accessToken(): Promise<string> {
    const cached = await this.#tokens.get();
    if (cached && cached.expires_at - TOKEN_SKEW_MS > this.#now().getTime()) {
      return cached.access_token;
    }

    if (cached?.refresh_token) {
      try {
        const refreshed = await this.#refresh(cached.refresh_token);
        await this.#tokens.set(refreshed);
        return refreshed.access_token;
      } catch (e) {
        this.#log("refresh_token failed, falling back to full login", {
          detail: e instanceof Error ? e.message : String(e),
        });
      }
    }

    const fresh = await this.#login();
    await this.#tokens.set(fresh);
    return fresh.access_token;
  }

  // ------------------------------------------------------------- requests

  async #request(
    method: string,
    path: string,
    options: { body?: unknown; retryOn401?: boolean } = {},
  ): Promise<{ status: number; json: unknown }> {
    const retryOn401 = options.retryOn401 ?? true;
    const token = await this.#accessToken();

    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      Origin: ORIGIN,
      "User-Agent": USER_AGENT,
      Accept: "application/json",
    };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";

    const response = await this.#fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });

    if (response.status === 401 && retryOn401) {
      // Token was rejected despite looking fresh. Drop it and go around once.
      await this.#tokens.clear();
      return await this.#request(method, path, { ...options, retryOn401: false });
    }

    if (response.status === 401) {
      throw new KitError("auth_failed", `${method} ${path} returned 401 after re-authenticating`);
    }

    if (response.status === 204) return { status: 204, json: null };

    const text = await response.text();

    if (!response.ok) {
      throw new KitError(
        "api_changed",
        `${method} ${path} returned ${response.status}: ${text.slice(0, 200)}`,
      );
    }

    if (text.length === 0) return { status: response.status, json: null };

    try {
      return { status: response.status, json: JSON.parse(text) };
    } catch {
      throw new KitError("api_changed", `${method} ${path} returned a non-JSON body`);
    }
  }

  // --------------------------------------------------------------- reads

  /**
   * All children in one call.
   *
   * The raw response also carries GPS location and address per child. It is
   * narrowed to four fields here, at the client boundary, so nothing
   * downstream can log, store, or return whereabouts.
   */
  async getProfiles(): Promise<Profile[]> {
    const { json } = await this.#request("GET", `/v1/accounts/${ACCOUNT_ID}/profiles/`);

    return itemsOf(json, "profiles").map((raw) => {
      const p = raw as Record<string, unknown>;
      if (typeof p.uid !== "string") {
        throw new KitError("api_changed", "a profile had no uid");
      }
      const activeRoutine = p.active_routine;
      return {
        id: typeof p.id === "number" ? p.id : Number(p.id),
        uid: p.uid,
        name: typeof p.name === "string" ? p.name : "",
        active_routine: typeof activeRoutine === "string" ? activeRoutine : null,
      };
    });
  }

  /** Routines for a child, including disabled ones, for the uid to name map. */
  async getRoutines(profileUid: string): Promise<Routine[]> {
    const { json } = await this.#request(
      "GET",
      `/v2/accounts/${ACCOUNT_UID}/profiles/${profileUid}/routines` +
        `?filter_app_rules=false&include_disabled=1`,
    );

    return itemsOf(json, "routines").flatMap((raw) => {
      const r = raw as Record<string, unknown>;
      if (typeof r.uid !== "string") return [];
      return [{ uid: r.uid, name: typeof r.name === "string" ? r.name : r.uid }];
    });
  }

  async getSchedules(profileUid: string, routineUid: string): Promise<Schedule[]> {
    const { json } = await this.#request(
      "GET",
      `/v2/accounts/${ACCOUNT_UID}/profiles/${profileUid}/routines/${routineUid}/schedules`,
    );

    return itemsOf(json, "schedules").map((raw) => toSchedule(raw));
  }

  // -------------------------------------------------------------- writes

  /**
   * Create an override schedule. This is the whole lock primitive.
   *
   * `overrides` is hard-coded true and asserted: a schedule POSTed without it
   * would become part of the family's permanent recurring rules.
   */
  async createOverride(
    profileUid: string,
    routineUid: string,
    window: {
      weekdays: [Weekday];
      start_time: string;
      duration_minutes: number;
      from_date: string;
      to_date: string;
    },
  ): Promise<Schedule> {
    // `overrides` is spread last so a caller-supplied field cannot displace it.
    const body = { ...window, overrides: true as const };

    const { json } = await this.#request(
      "POST",
      `/v2/accounts/${ACCOUNT_UID}/profiles/${profileUid}/routines/${routineUid}/schedules`,
      { body },
    );

    const created = toSchedule(json);
    if (created.overrides !== true) {
      throw new KitError(
        "api_changed",
        "created schedule came back with overrides false — refusing to treat it as a lock",
      );
    }
    return created;
  }

  /**
   * Delete an override schedule.
   *
   * Takes the schedule object rather than a uid on purpose. A wrong uid here
   * silently destroys a permanent rule, with no undo and no audit trail in
   * Qustodio, so the object just read from the API is what gets checked.
   */
  async deleteOverride(
    profileUid: string,
    routineUid: string,
    schedule: Schedule,
  ): Promise<void> {
    if (schedule?.overrides !== true) {
      throw new ScheduleGuardError(
        `Refusing to DELETE schedule ${schedule?.uid ?? "<no uid>"}: overrides is ` +
          `${JSON.stringify(schedule?.overrides)}, not true. Only overrides created as ` +
          `temporary switches may be deleted; everything else is a permanent family rule.`,
      );
    }
    if (typeof schedule.uid !== "string" || schedule.uid.length === 0) {
      throw new ScheduleGuardError("Refusing to DELETE a schedule with no uid");
    }

    await this.#request(
      "DELETE",
      `/v2/accounts/${ACCOUNT_UID}/profiles/${profileUid}/routines/${routineUid}` +
        `/schedules/${schedule.uid}`,
    );
  }

  /** Cheap authenticated GET, for /api/health. */
  async ping(): Promise<void> {
    await this.getProfiles();
  }
}

export function toSchedule(raw: unknown): Schedule {
  if (!raw || typeof raw !== "object") {
    throw new KitError("api_changed", "schedule was not an object");
  }
  const s = stripLocation(raw as Record<string, unknown>);

  if (typeof s.uid !== "string") {
    throw new KitError("api_changed", "schedule had no uid");
  }
  if (typeof s.overrides !== "boolean") {
    // Never guess this one. Defaulting it either way is a foot-gun: false would
    // hide a lock, true would arm the delete path against a permanent rule.
    throw new KitError("api_changed", `schedule ${s.uid} had no boolean overrides field`);
  }
  if (typeof s.from_date !== "string" || typeof s.start_time !== "string") {
    throw new KitError("api_changed", `schedule ${s.uid} was missing from_date or start_time`);
  }

  return {
    uid: s.uid,
    duration_minutes: typeof s.duration_minutes === "number" ? s.duration_minutes : 0,
    weekdays: Array.isArray(s.weekdays) ? (s.weekdays as string[]) : [],
    start_time: s.start_time,
    from_date: s.from_date,
    to_date: typeof s.to_date === "string" ? s.to_date : null,
    overrides: s.overrides,
    enabled: typeof s.enabled === "boolean" ? s.enabled : true,
  };
}

/** KV-backed token store. */
export function kvTokenStore(kv: KVNamespace): TokenStore {
  return {
    async get() {
      return await kv.get<CachedToken>("token", "json");
    },
    async set(token) {
      await kv.put("token", JSON.stringify(token));
    },
    async clear() {
      await kv.delete("token");
    },
  };
}
