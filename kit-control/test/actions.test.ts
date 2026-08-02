import { beforeEach, describe, expect, it } from "vitest";

import { lockAll, lockChild, unlockChild } from "../src/actions.js";
import { CHILDREN } from "../src/config.js";
import { KitError } from "../src/errors.js";
import { readStatus } from "../src/status.js";
import { memoryStore, type Store } from "../src/store.js";
import { FakeQustodio } from "./fake-qustodio.js";
import { SATURDAY_2210, makeClient } from "./helpers.js";

const coen = CHILDREN.find((c) => c.slug === "coen")!;
const selah = CHILDREN.find((c) => c.slug === "selah")!;
const taysha = CHILDREN.find((c) => c.slug === "taysha")!;

describe("lock", () => {
  let fake: FakeQustodio;
  let store: Store;

  beforeEach(() => {
    fake = new FakeQustodio();
    store = memoryStore();
  });

  it("creates an override on the child's Do Chores routine", async () => {
    const result = await lockChild(makeClient(fake), store, coen, SATURDAY_2210);

    expect(result.status).toBe("locked");
    expect(result.ok).toBe(true);

    const created = fake.creates();
    expect(created).toHaveLength(1);
    expect(created[0]?.path).toContain(coen.lockRoutineUid);
    expect(created[0]?.body).toMatchObject({
      overrides: true,
      weekdays: ["SA"],
      start_time: "22:10",
      from_date: "2026-08-01",
      to_date: "2026-08-02",
      duration_minutes: 480,
    });
  });

  it("locks into Do Chores rather than bedtime, and never writes to bedtime", async () => {
    await lockChild(makeClient(fake), store, coen, SATURDAY_2210);
    expect(fake.writes().every((w) => !w.path.includes(coen.bedtimeRoutineUid))).toBe(true);
  });

  it("records the created schedule in KV so status can call it ours", async () => {
    await lockChild(makeClient(fake), store, coen, SATURDAY_2210);

    const record = await store.getOverride(coen.profileUid);
    expect(record?.routineUid).toBe(coen.lockRoutineUid);
    expect(record?.expiresAt).toBe("2026-08-02T06:10:00-06:00");

    const status = await readStatus(makeClient(fake), store, { now: SATURDAY_2210 });
    const child = status.children.find((c) => c.slug === "coen")!;
    expect(child.locked).toBe(true);
    expect(child.source).toBe("kit");
  });

  it("speaks the time the lock ends", async () => {
    const result = await lockChild(makeClient(fake), store, coen, SATURDAY_2210);
    expect(result.spoken).toBe("Coen is locked until 6:10 AM.");
  });

  it("returns already_locked and issues no write when a lock is in effect", async () => {
    fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "22:10:00" });

    const result = await lockChild(makeClient(fake), store, coen, SATURDAY_2210);

    expect(result.status).toBe("already_locked");
    expect(result.ok).toBe(false);
    expect(fake.writes()).toHaveLength(0);
  });

  it("never stacks overrides on a child locked from the Qustodio app", async () => {
    fake.seedOverride(`${coen.slug}-play`, { from_date: "2026-08-01", start_time: "22:10:00" });
    const result = await lockChild(makeClient(fake), store, coen, SATURDAY_2210);
    expect(result.status).toBe("already_locked");
    expect(fake.creates()).toHaveLength(0);
  });

  it("refuses a child whose lock prerequisite is outstanding", async () => {
    await expect(lockChild(makeClient(fake), store, taysha, SATURDAY_2210)).rejects.toMatchObject({
      code: "lock_disabled",
    });
    expect(fake.writes()).toHaveLength(0);
  });

  it("reports verify_failed when the write is accepted but does not land", async () => {
    const swallowing = new FakeQustodio({ swallowCreates: true });

    await expect(
      lockChild(makeClient(swallowing), store, coen, SATURDAY_2210),
    ).rejects.toMatchObject({ code: "verify_failed" });
  });
});

describe("unlock", () => {
  let fake: FakeQustodio;
  let store: Store;

  beforeEach(() => {
    fake = new FakeQustodio();
    store = memoryStore();
  });

  it("deletes the override this app created", async () => {
    await lockChild(makeClient(fake), store, coen, SATURDAY_2210);
    const created = fake.creates();
    expect(created).toHaveLength(1);

    const result = await unlockChild(makeClient(fake), store, coen, SATURDAY_2210);

    expect(result.status).toBe("unlocked");
    expect(fake.deletes()).toHaveLength(1);
    expect(await store.getOverride(coen.profileUid)).toBeNull();
  });

  it("deletes an override set in the Qustodio app, which KV knows nothing about", async () => {
    const seeded = fake.seedOverride(`${coen.slug}-play`, {
      from_date: "2026-08-01",
      start_time: "22:10:00",
    });

    const result = await unlockChild(makeClient(fake), store, coen, SATURDAY_2210);

    expect(result.status).toBe("unlocked");
    expect(fake.schedules.get(`${coen.slug}-play`)?.map((s) => s.uid)).not.toContain(seeded.uid);
  });

  it("returns already_unlocked and issues no delete when nothing is overridden", async () => {
    const result = await unlockChild(makeClient(fake), store, coen, SATURDAY_2210);

    expect(result.status).toBe("already_unlocked");
    expect(result.ok).toBe(true);
    expect(fake.deletes()).toHaveLength(0);
  });

  it("is not an error during a scheduled bedtime, and says so", async () => {
    fake.activeRoutine.set(coen.profileUid, coen.bedtimeRoutineUid);

    const result = await unlockChild(makeClient(fake), store, coen, SATURDAY_2210);

    expect(result.status).toBe("already_unlocked");
    expect(result.ok).toBe(true);
    expect(result.spoken).toMatch(/bedtime is still scheduled/i);
  });

  it("unlocking twice is safe and touches nothing the second time", async () => {
    await lockChild(makeClient(fake), store, coen, SATURDAY_2210);
    await unlockChild(makeClient(fake), store, coen, SATURDAY_2210);
    const deletesAfterFirst = fake.deletes().length;

    const second = await unlockChild(makeClient(fake), store, coen, SATURDAY_2210);

    expect(second.status).toBe("already_unlocked");
    expect(fake.deletes()).toHaveLength(deletesAfterFirst);
  });

  it("leaves the permanent schedules untouched", async () => {
    await lockChild(makeClient(fake), store, coen, SATURDAY_2210);
    await unlockChild(makeClient(fake), store, coen, SATURDAY_2210);

    expect(fake.schedules.get(coen.lockRoutineUid)?.map((s) => s.uid)).toEqual(["coen-permanent"]);
    expect(fake.schedules.get(coen.bedtimeRoutineUid)?.map((s) => s.uid)).toEqual(["coen-bedtime"]);
  });

  it("works for a child whose lock is gated — unlock is never blocked", async () => {
    fake.seedOverride(taysha.lockRoutineUid, {
      from_date: "2026-08-01",
      start_time: "22:10:00",
    });

    const result = await unlockChild(makeClient(fake), store, taysha, SATURDAY_2210);
    expect(result.status).toBe("unlocked");
  });
});

describe("lock everyone", () => {
  let store: Store;

  beforeEach(() => {
    store = memoryStore();
  });

  it("reports the other children accurately when one fails", async () => {
    // Taysha is gated by config; make Selah's write fail too, leaving Coen.
    const fake = new FakeQustodio({ failWritesFor: [selah.lockRoutineUid] });

    const result = await lockAll(makeClient(fake), store, SATURDAY_2210);

    expect(result.ok).toBe(false);
    expect(result.results.map((r) => [r.name, r.status])).toEqual([
      ["Taysha", "error"],
      ["Selah", "error"],
      ["Coen", "locked"],
    ]);
    expect(result.results[0]?.detail).toMatch(/lock_disabled/);
    expect(result.results[1]?.detail).toMatch(/api_changed/);
  });

  it("never hides a failure behind a blanket done", async () => {
    const fake = new FakeQustodio({ failWritesFor: [selah.lockRoutineUid] });
    const result = await lockAll(makeClient(fake), store, SATURDAY_2210);

    expect(result.spoken).toMatch(/Coen/);
    expect(result.spoken).toMatch(/Couldn't lock/);
    expect(result.spoken).toMatch(/Taysha/);
    expect(result.spoken).toMatch(/Selah/);
  });

  it("keeps going after a failure rather than aborting the run", async () => {
    const fake = new FakeQustodio({ failWritesFor: [selah.lockRoutineUid] });
    await lockAll(makeClient(fake), store, SATURDAY_2210);

    // Coen comes last in the config order, so his lock proves the loop continued.
    const coenSchedules = fake.schedules.get(coen.lockRoutineUid) ?? [];
    expect(coenSchedules.some((s) => s.overrides === true)).toBe(true);
  });

  it("reports already_locked per child without writing", async () => {
    const fake = new FakeQustodio();
    fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "22:10:00" });

    const result = await lockAll(makeClient(fake), store, SATURDAY_2210);
    const coenResult = result.results.find((r) => r.slug === "coen")!;

    expect(coenResult.status).toBe("already_locked");
    expect(fake.creates().every((c) => !c.path.includes(coen.lockRoutineUid))).toBe(true);
  });

  it("is not ok while any child's prerequisite is outstanding", async () => {
    const fake = new FakeQustodio();
    const result = await lockAll(makeClient(fake), store, SATURDAY_2210);

    expect(result.ok).toBe(false);
    expect(result.results.find((r) => r.slug === "taysha")?.status).toBe("error");
    expect(result.results.find((r) => r.slug === "selah")?.status).toBe("locked");
    expect(result.results.find((r) => r.slug === "coen")?.status).toBe("locked");
  });
});

describe("typed errors", () => {
  it("maps lock_disabled to a precondition status", () => {
    expect(new KitError("lock_disabled", "x").status).toBe(412);
  });

  it("keeps already_locked out of the error channel entirely", async () => {
    const fake = new FakeQustodio();
    fake.seedOverride(coen.lockRoutineUid, { from_date: "2026-08-01", start_time: "22:10:00" });

    // It resolves rather than throwing: it is a refusal to write, not a failure.
    const result = await lockChild(makeClient(fake), memoryStore(), coen, SATURDAY_2210);
    expect(result.status).toBe("already_locked");
  });
});
