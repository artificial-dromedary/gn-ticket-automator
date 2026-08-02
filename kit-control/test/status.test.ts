import { beforeEach, describe, expect, it } from "vitest";

import { CHILDREN } from "../src/config.js";
import { readStatus } from "../src/status.js";
import { memoryStore, type Store } from "../src/store.js";
import { FakeQustodio } from "./fake-qustodio.js";
import { SATURDAY_2210, makeClient } from "./helpers.js";

const coen = CHILDREN.find((c) => c.slug === "coen")!;
const selah = CHILDREN.find((c) => c.slug === "selah")!;
const taysha = CHILDREN.find((c) => c.slug === "taysha")!;

describe("status", () => {
  let fake: FakeQustodio;
  let store: Store;

  beforeEach(() => {
    fake = new FakeQustodio();
    store = memoryStore();
  });

  const read = () => readStatus(makeClient(fake), store, { now: SATURDAY_2210 });

  it("reports everyone unlocked when only permanent schedules exist", async () => {
    const status = await read();
    expect(status.children.map((c) => c.locked)).toEqual([false, false, false]);
    expect(status.children.map((c) => c.source)).toEqual([null, null, null]);
  });

  it("keys locked off the overrides flag, not active_routine", async () => {
    // Do Chores is running as a normal scheduled routine. That is not a lock.
    fake.activeRoutine.set(coen.profileUid, coen.lockRoutineUid);

    const status = await read();
    const child = status.children.find((c) => c.slug === "coen")!;

    expect(child.activeRoutine).toBe("Do Chores");
    expect(child.locked).toBe(false);
  });

  it("reports locked when an override is inside its window, whatever active_routine says", async () => {
    fake.activeRoutine.set(coen.profileUid, null);
    fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "22:10:00" });

    const child = (await read()).children.find((c) => c.slug === "coen")!;
    expect(child.locked).toBe(true);
    expect(child.activeRoutine).toBeNull();
  });

  it("reports the local end of the window as lockedUntil", async () => {
    fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "22:10:00" });
    const child = (await read()).children.find((c) => c.slug === "coen")!;
    // 22:10 + 480 minutes = 06:10 the next local day.
    expect(child.lockedUntil).toBe("2026-08-02T06:10:00-06:00");
  });

  it("ignores an override whose window has already passed", async () => {
    fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "06:00:00" });
    const child = (await read()).children.find((c) => c.slug === "coen")!;
    expect(child.locked).toBe(false);
  });

  it("finds an override set on a routine other than Do Chores", async () => {
    // A lock set from the Qustodio app can live on any routine.
    fake.seedOverride(`${coen.slug}-play`, { from_date: "2026-08-01", start_time: "22:10:00" });
    const child = (await read()).children.find((c) => c.slug === "coen")!;
    expect(child.locked).toBe(true);
  });

  describe("source", () => {
    it("is kit when KV names the exact schedule", async () => {
      const seeded = fake.seedOverride(coen.lockRoutineUid, {
        from_date: "2026-08-01",
        start_time: "22:10:00",
      });
      await store.putOverride(coen.profileUid, {
        routineUid: coen.lockRoutineUid,
        scheduleUid: seeded.uid,
        createdAt: "2026-08-01T22:10:00-06:00",
        expiresAt: "2026-08-02T06:10:00-06:00",
      });

      const child = (await read()).children.find((c) => c.slug === "coen")!;
      expect(child.source).toBe("kit");
    });

    it("is qustodio when KV knows nothing about it", async () => {
      fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "22:10:00" });
      const child = (await read()).children.find((c) => c.slug === "coen")!;
      expect(child.source).toBe("qustodio");
    });

    it("is qustodio when KV names a different schedule", async () => {
      fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "22:10:00" });
      await store.putOverride(coen.profileUid, {
        routineUid: coen.lockRoutineUid,
        scheduleUid: "some-other-schedule",
        createdAt: "2026-08-01T22:10:00-06:00",
        expiresAt: "2026-08-02T06:10:00-06:00",
      });

      const child = (await read()).children.find((c) => c.slug === "coen")!;
      expect(child.source).toBe("qustodio");
    });
  });

  it("labels a scheduled bedtime distinctly", async () => {
    fake.activeRoutine.set(selah.profileUid, selah.bedtimeRoutineUid);
    const child = (await read()).children.find((c) => c.slug === "selah")!;
    expect(child.activeRoutineIsBedtime).toBe(true);
    expect(child.activeRoutine).toBe("Bedtime");
  });

  it("surfaces the lock gate for a child whose prerequisite is outstanding", async () => {
    const status = await read();
    const t = status.children.find((c) => c.slug === "taysha")!;
    const c = status.children.find((c) => c.slug === "coen")!;

    expect(t.lockAvailable).toBe(false);
    expect(t.lockBlockedReason).toMatch(/block_type/);
    expect(c.lockAvailable).toBe(true);
    expect(c.lockBlockedReason).toBeNull();
    expect(taysha.lockBlockedReason).not.toBeNull();
  });

  describe("stale override cleanup", () => {
    it("deletes an override whose window closed more than 24 hours ago", async () => {
      const stale = fake.seedOverride(coen.lockRoutineUid, {
        from_date: "2026-07-29",
        start_time: "22:10:00",
      });

      await read();

      expect(fake.deletes()).toHaveLength(1);
      expect(fake.schedules.get(coen.lockRoutineUid)?.map((s) => s.uid)).not.toContain(stale.uid);
    });

    it("leaves the permanent schedules alone", async () => {
      fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-07-29", start_time: "22:10:00" });
      await read();
      expect(fake.schedules.get(coen.lockRoutineUid)?.map((s) => s.uid)).toContain(
        "coen-permanent",
      );
      expect(fake.schedules.get(coen.bedtimeRoutineUid)?.map((s) => s.uid)).toContain(
        "coen-bedtime",
      );
    });

    it("leaves a recently expired override alone", async () => {
      // Closed a few hours ago, inside the grace period.
      fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "06:00:00" });
      await read();
      expect(fake.deletes()).toHaveLength(0);
    });

    it("never deletes the override currently in effect", async () => {
      fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "22:10:00" });
      await read();
      expect(fake.deletes()).toHaveLength(0);
    });
  });

  it("does not write at all on a plain read with nothing stale", async () => {
    await read();
    expect(fake.writes()).toHaveLength(0);
  });
});
