/**
 * The safety rails from section 5. These are the tests that matter most: a
 * wrong delete here silently destroys a permanent family rule, with no undo
 * and no audit trail in Qustodio.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { CHILDREN } from "../src/config.js";
import {
  QustodioClient,
  ScheduleGuardError,
  findAuthorizationCode,
  stripLocation,
  toSchedule,
  type Schedule,
} from "../src/qustodio.js";
import { FakeQustodio } from "./fake-qustodio.js";
import { makeClient, memoryTokenStore } from "./helpers.js";

const coen = CHILDREN.find((c) => c.slug === "coen")!;

const permanent: Schedule = {
  uid: "coen-permanent",
  duration_minutes: 90,
  weekdays: ["MO"],
  start_time: "16:00:00",
  from_date: "2026-01-09",
  to_date: null,
  overrides: false,
  enabled: true,
};

describe("delete guard", () => {
  let fake: FakeQustodio;
  let client: QustodioClient;

  beforeEach(() => {
    fake = new FakeQustodio();
    client = makeClient(fake);
  });

  it("throws when handed a schedule with overrides: false", async () => {
    await expect(
      client.deleteOverride(coen.profileUid, coen.lockRoutineUid, permanent),
    ).rejects.toThrow(ScheduleGuardError);
  });

  it("issues no DELETE request when the guard trips", async () => {
    await client
      .deleteOverride(coen.profileUid, coen.lockRoutineUid, permanent)
      .catch(() => {});
    expect(fake.deletes()).toHaveLength(0);
  });

  it("leaves the permanent rule in place when the guard trips", async () => {
    await client
      .deleteOverride(coen.profileUid, coen.lockRoutineUid, permanent)
      .catch(() => {});
    const remaining = fake.schedules.get(coen.lockRoutineUid) ?? [];
    expect(remaining.map((s) => s.uid)).toContain("coen-permanent");
    expect(remaining.every((s) => s.overrides === false)).toBe(true);
  });

  it("throws when overrides is missing entirely", async () => {
    const missing = { ...permanent } as Partial<Schedule>;
    delete missing.overrides;
    await expect(
      client.deleteOverride(coen.profileUid, coen.lockRoutineUid, missing as Schedule),
    ).rejects.toThrow(ScheduleGuardError);
  });

  it("throws on a schedule with no uid, even when overrides is true", async () => {
    await expect(
      client.deleteOverride(coen.profileUid, coen.lockRoutineUid, {
        ...permanent,
        uid: "",
        overrides: true,
      }),
    ).rejects.toThrow(ScheduleGuardError);
  });

  it("deletes when the schedule really is an override", async () => {
    const override = fake.seedOverride(coen.lockRoutineUid, {
      from_date: "2026-08-01",
      start_time: "22:10:00",
    });
    await client.deleteOverride(coen.profileUid, coen.lockRoutineUid, override);
    expect(fake.deletes()).toHaveLength(1);
    expect(fake.schedules.get(coen.lockRoutineUid)?.map((s) => s.uid)).not.toContain(override.uid);
  });
});

describe("create guard", () => {
  it("always sends overrides: true", async () => {
    const fake = new FakeQustodio();
    const client = makeClient(fake);

    await client.createOverride(coen.profileUid, coen.lockRoutineUid, {
      weekdays: ["SA"],
      start_time: "22:10",
      duration_minutes: 480,
      from_date: "2026-08-01",
      to_date: "2026-08-02",
    });

    const created = fake.creates();
    expect(created).toHaveLength(1);
    expect((created[0]?.body as Record<string, unknown>).overrides).toBe(true);
  });

  it("refuses to treat a response with overrides false as a lock", async () => {
    const fake = new FakeQustodio();
    // A server that echoes overrides: false back.
    const client = new QustodioClient(
      {
        email: "e",
        password: "p",
        clientId: "c",
        clientSecret: "s",
      },
      memoryTokenStore(),
      {
        fetch: async (input, init) => {
          const url = new URL(typeof input === "string" ? input : (input as Request).url);
          if (url.pathname.endsWith("/schedules") && (init?.method ?? "GET") === "POST") {
            return new Response(JSON.stringify({ ...permanent, uid: "x", overrides: false }), {
              status: 201,
              headers: { "Content-Type": "application/json" },
            });
          }
          return fake.fetch(input, init);
        },
        now: () => new Date("2026-08-02T04:10:00Z"),
        sleep: async () => {},
        log: () => {},
      },
    );

    await expect(
      client.createOverride(coen.profileUid, coen.lockRoutineUid, {
        weekdays: ["SA"],
        start_time: "22:10",
        duration_minutes: 480,
        from_date: "2026-08-01",
        to_date: "2026-08-02",
      }),
    ).rejects.toThrow(/overrides false/);
  });
});

describe("schedule parsing", () => {
  it("refuses to guess a missing overrides flag", () => {
    // Defaulting either way is a foot-gun: false hides a lock, true arms the
    // delete path against a permanent rule.
    expect(() => toSchedule({ uid: "x", from_date: "2026-08-01", start_time: "22:10" })).toThrow(
      /overrides/,
    );
  });

  it("keeps a real schedule intact", () => {
    expect(toSchedule({ ...permanent })).toEqual(permanent);
  });
});

describe("location stripping", () => {
  it("removes whereabouts at any depth", () => {
    const stripped = stripLocation({
      name: "Coen",
      status: {
        location: { latitude: 1, longitude: 2 },
        address: "somewhere",
        battery: 80,
      },
      nested: [{ lat: 1, lng: 2, keep: true }],
    });

    expect(JSON.stringify(stripped)).not.toMatch(/latitude|longitude|address|lat|lng/);
    expect(stripped).toEqual({ name: "Coen", status: { battery: 80 }, nested: [{ keep: true }] });
  });

  it("never lets a profile's location past the client boundary", async () => {
    const fake = new FakeQustodio();
    const client = makeClient(fake);

    const profiles = await client.getProfiles();

    expect(profiles).toHaveLength(CHILDREN.length);
    expect(JSON.stringify(profiles)).not.toMatch(/latitude|longitude|address|Somewhere St/);
    expect(Object.keys(profiles[0] ?? {}).sort()).toEqual([
      "active_routine",
      "id",
      "name",
      "uid",
    ]);
  });
});

describe("authorization_code extraction", () => {
  it("finds the code whatever the field is called", () => {
    expect(
      findAuthorizationCode({
        redirect_url: "https://family.qustodio.com/parents-app?authorization_code=abc123&state=x",
      }),
    ).toBe("abc123");
    expect(findAuthorizationCode({ data: { nested: ["?authorization_code=deep"] } })).toBe("deep");
  });

  it("returns null when no field carries one", () => {
    expect(findAuthorizationCode({ status: "ok", message: "no code here" })).toBeNull();
  });

  it("url-decodes the value", () => {
    expect(findAuthorizationCode({ u: "?authorization_code=a%2Bb" })).toBe("a+b");
  });
});
