/**
 * A stand-in for Qustodio's private API, backed by an in-memory model.
 *
 * It reproduces the shapes the brief captured, including the parts that make
 * this app risky: permanent `overrides: false` schedules sitting in the same
 * list as temporary ones, and a profiles response carrying GPS.
 */

import { ACCOUNT_ID, ACCOUNT_UID, CHILDREN } from "../src/config.js";
import type { Schedule } from "../src/qustodio.js";

export interface FakeRequest {
  method: string;
  path: string;
  body?: unknown;
}

export interface FakeOptions {
  /** Routine uids that reject writes, to exercise per-child failure. */
  failWritesFor?: string[];
  /** Make created overrides invisible on re-read, to exercise verify_failed. */
  swallowCreates?: boolean;
}

let counter = 0;
const nextUid = (): string => `sched-${(counter += 1)}`;

export class FakeQustodio {
  /** routineUid -> schedules */
  readonly schedules = new Map<string, Schedule[]>();
  /** profileUid -> [{uid, name}] */
  readonly routines = new Map<string, Array<{ uid: string; name: string }>>();
  /** profileUid -> active routine uid or null */
  readonly activeRoutine = new Map<string, string | null>();

  readonly requests: FakeRequest[] = [];
  loginCount = 0;

  #options: FakeOptions;

  constructor(options: FakeOptions = {}) {
    this.#options = options;

    for (const child of CHILDREN) {
      this.routines.set(child.profileUid, [
        { uid: child.lockRoutineUid, name: "Do Chores" },
        { uid: child.bedtimeRoutineUid, name: "Bedtime" },
        { uid: `${child.slug}-play`, name: "Play time" },
      ]);
      this.activeRoutine.set(child.profileUid, null);

      // A permanent recurring rule on the lock routine. Deleting one of these
      // is the disaster the guard exists to prevent, so every test runs with
      // them present.
      this.schedules.set(child.lockRoutineUid, [
        {
          uid: `${child.slug}-permanent`,
          duration_minutes: 90,
          weekdays: ["MO", "TU", "WE", "TH", "FR"],
          start_time: "16:00:00",
          from_date: "2026-01-09",
          to_date: null,
          overrides: false,
          enabled: true,
        },
      ]);
      this.schedules.set(child.bedtimeRoutineUid, [
        {
          uid: `${child.slug}-bedtime`,
          duration_minutes: 600,
          weekdays: ["MO", "TU", "WE", "TH", "FR", "SA", "SU"],
          start_time: "21:00:00",
          from_date: "2026-01-09",
          to_date: null,
          overrides: false,
          enabled: true,
        },
      ]);
      this.schedules.set(`${child.slug}-play`, []);
    }
  }

  /** Add an override directly, as if it had been set in the Qustodio app. */
  seedOverride(routineUid: string, schedule: Partial<Schedule> & { from_date: string; start_time: string }): Schedule {
    const created: Schedule = {
      uid: schedule.uid ?? nextUid(),
      duration_minutes: schedule.duration_minutes ?? 480,
      weekdays: schedule.weekdays ?? ["SA"],
      start_time: schedule.start_time,
      from_date: schedule.from_date,
      to_date: schedule.to_date ?? null,
      overrides: schedule.overrides ?? true,
      enabled: schedule.enabled ?? true,
    };
    this.schedules.set(routineUid, [...(this.schedules.get(routineUid) ?? []), created]);
    return created;
  }

  /** Schedule mutations only. The auth POSTs are not writes to family state. */
  writes(): FakeRequest[] {
    return this.requests.filter(
      (r) => (r.method === "POST" || r.method === "DELETE") && r.path.includes("/schedules"),
    );
  }

  deletes(): FakeRequest[] {
    return this.writes().filter((r) => r.method === "DELETE");
  }

  creates(): FakeRequest[] {
    return this.writes().filter((r) => r.method === "POST");
  }

  /** Drop-in replacement for `fetch`. */
  readonly fetch: typeof fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : (input as Request).url);
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname;
    const body = init?.body ? safeParse(String(init.body)) : undefined;

    this.requests.push({ method, path, body });

    if (path === "/en/sso/do_login/") {
      this.loginCount += 1;
      return jsonResponse(200, {
        status: "ok",
        // The real field name was never captured, so the fake picks an
        // arbitrary one: the client finds the code wherever it lives.
        redirect: `https://family.qustodio.com/parents-app?authorization_code=fake-code-123&state=abc`,
      });
    }

    if (path === "/v1/oauth2/access_token") {
      return jsonResponse(200, {
        access_token: "fake-access-token",
        expires_in: 3600,
        token_type: "Bearer",
        scope: "parent",
      });
    }

    if (method === "GET" && path === `/v1/accounts/${ACCOUNT_ID}/profiles/`) {
      return jsonResponse(
        200,
        CHILDREN.map((child) => ({
          id: child.profileId,
          uid: child.profileUid,
          name: child.name,
          active_routine: this.activeRoutine.get(child.profileUid) ?? null,
          // The real response carries these. They must never leave the client.
          status: {
            location: { latitude: 53.5461, longitude: -113.4938, accuracy: 12 },
            address: "123 Somewhere St, Edmonton AB",
          },
        })),
      );
    }

    const routinesMatch = new RegExp(
      `^/v2/accounts/${ACCOUNT_UID}/profiles/([^/]+)/routines$`,
    ).exec(path);
    if (method === "GET" && routinesMatch) {
      const list = this.routines.get(routinesMatch[1] ?? "") ?? [];
      return jsonResponse(200, { total_count: list.length, items_list: list });
    }

    const schedulesMatch = new RegExp(
      `^/v2/accounts/${ACCOUNT_UID}/profiles/([^/]+)/routines/([^/]+)/schedules$`,
    ).exec(path);
    if (schedulesMatch) {
      const routineUid = schedulesMatch[2] ?? "";

      if (method === "GET") {
        const list = this.schedules.get(routineUid) ?? [];
        return jsonResponse(200, { total_count: list.length, items_list: list });
      }

      if (method === "POST") {
        if (this.#options.failWritesFor?.includes(routineUid)) {
          return jsonResponse(500, { error: "internal" });
        }
        const payload = body as Record<string, unknown>;
        const created: Schedule = {
          uid: nextUid(),
          duration_minutes: Number(payload.duration_minutes),
          weekdays: payload.weekdays as string[],
          start_time: String(payload.start_time),
          from_date: String(payload.from_date),
          to_date: (payload.to_date as string) ?? null,
          overrides: payload.overrides === true,
          enabled: true,
        };
        if (!this.#options.swallowCreates) {
          this.schedules.set(routineUid, [...(this.schedules.get(routineUid) ?? []), created]);
        }
        return jsonResponse(201, created);
      }
    }

    const deleteMatch = new RegExp(
      `^/v2/accounts/${ACCOUNT_UID}/profiles/([^/]+)/routines/([^/]+)/schedules/([^/]+)$`,
    ).exec(path);
    if (method === "DELETE" && deleteMatch) {
      const routineUid = deleteMatch[2] ?? "";
      const scheduleUid = deleteMatch[3] ?? "";
      const list = this.schedules.get(routineUid) ?? [];
      this.schedules.set(
        routineUid,
        list.filter((s) => s.uid !== scheduleUid),
      );
      return new Response(null, { status: 204 });
    }

    return jsonResponse(404, { error: "not_found", path });
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
