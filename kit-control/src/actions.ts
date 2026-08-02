/**
 * Lock, unlock, lock everyone.
 *
 * Every write is followed by a read that confirms the state actually changed.
 * A blanket "done" that hides a failure is the exact bug this app exists to
 * avoid, so nothing here reports success optimistically.
 */

import { CHILDREN, LOCK_DURATION_MINUTES, type ChildConfig } from "./config.js";
import { KitError } from "./errors.js";
import { QustodioClient, type Schedule } from "./qustodio.js";
import { childStatusFrom, invalidateStatusCache, readChildState, type ChildStatus } from "./status.js";
import type { Store } from "./store.js";
import { lockWindow, toLocalClock, toLocalIso, windowContains, windowEnd } from "./time.js";

/** Propagation measured at under 7 seconds on create, immediate on delete. */
const VERIFY_DELAY_MS = 3_000;

export type LockOutcome = "locked" | "already_locked";
export type UnlockOutcome = "unlocked" | "already_unlocked";

export interface ActionResult {
  ok: boolean;
  slug: string;
  name: string;
  status: LockOutcome | UnlockOutcome | "error";
  detail?: string;
  child?: ChildStatus;
  /** Short natural sentence for Siri. Shortcuts need no logic of their own. */
  spoken: string;
}

/**
 * Lock one child.
 *
 * The override is written into the child's Do Chores routine, which is a full
 * device block for every child this is enabled for — so a lock during a
 * scheduled bedtime is never a downgrade, and one fixed duration is correct
 * regardless of what else is running.
 */
export async function lockChild(
  client: QustodioClient,
  store: Store,
  child: ChildConfig,
  now: Date = client.now(),
): Promise<ActionResult> {
  if (child.lockBlockedReason !== null) {
    throw new KitError("lock_disabled", child.lockBlockedReason);
  }

  // 1. Never stack overrides.
  const before = await readChildState(client, child, null, now);
  if (before.active) {
    const status = await childStatusFrom(before, store, now);
    return {
      ok: false,
      slug: child.slug,
      name: child.name,
      status: "already_locked",
      child: status,
      spoken: `${child.name} is already locked${
        status.lockedUntil ? ` until ${toLocalClock(windowEnd(before.active.schedule))}` : ""
      }.`,
    };
  }

  // 2. Create the override.
  const window = lockWindow(now, LOCK_DURATION_MINUTES);
  const created = await client.createOverride(child.profileUid, child.lockRoutineUid, window);
  const until = windowEnd(created);

  // 3. Remember it, so status can tell a lock this app set from one set in the
  //    Qustodio app.
  await store.putOverride(child.profileUid, {
    routineUid: child.lockRoutineUid,
    scheduleUid: created.uid,
    createdAt: toLocalIso(now),
    expiresAt: toLocalIso(until),
  });
  await invalidateStatusCache(store);

  // 4. Verify: read it back, once more after another wait if it has not landed.
  const confirmed = await verify(client, () => scheduleExists(client, child, created.uid), 2);
  if (!confirmed) {
    throw new KitError(
      "verify_failed",
      `The override for ${child.name} was accepted but did not appear on re-read. ` +
        `Check the Qustodio app before assuming ${child.name} is locked.`,
    );
  }

  const after = await readChildState(client, child, null, now);
  return {
    ok: true,
    slug: child.slug,
    name: child.name,
    status: "locked",
    child: await childStatusFrom(after, store, now),
    spoken: `${child.name} is locked until ${toLocalClock(until)}.`,
  };
}

/**
 * Unlock one child.
 *
 * Deletes the override this app created when KV knows it, otherwise finds the
 * live override by scanning. Unlocking when nothing is overridden is not an
 * error: during a scheduled bedtime it legitimately does nothing.
 */
export async function unlockChild(
  client: QustodioClient,
  store: Store,
  child: ChildConfig,
  now: Date = client.now(),
): Promise<ActionResult> {
  const state = await readChildState(client, child, null, now);
  const record = await store.getOverride(child.profileUid);

  // Prefer the schedule KV points at; fall back to whatever override is live.
  // Either way the object handed to the delete came from the API just now, and
  // the client re-checks `overrides === true` before it fires.
  const target =
    (record
      ? state.overrides.find(
          (o) => o.schedule.uid === record.scheduleUid && windowContains(o.schedule, now),
        )
      : undefined) ?? state.active;

  if (!target) {
    await store.deleteOverride(child.profileUid);
    const status = await childStatusFrom(state, store, now);
    return {
      ok: true,
      slug: child.slug,
      name: child.name,
      status: "already_unlocked",
      child: status,
      spoken: status.activeRoutineIsBedtime
        ? `${child.name} had no lock to remove. Bedtime is still scheduled.`
        : `${child.name} was already unlocked.`,
    };
  }

  await client.deleteOverride(child.profileUid, target.routineUid, target.schedule);
  await store.deleteOverride(child.profileUid);
  await invalidateStatusCache(store);

  // Verify: no override may remain inside its window.
  const cleared = await verify(client, async () => {
    const after = await readChildState(client, child, null, now);
    return after.active === null;
  }, 2);

  if (!cleared) {
    throw new KitError(
      "verify_failed",
      `The delete for ${child.name} was accepted but an override is still in effect. ` +
        `Check the Qustodio app.`,
    );
  }

  const after = await readChildState(client, child, null, now);
  const status = await childStatusFrom(after, store, now);
  return {
    ok: true,
    slug: child.slug,
    name: child.name,
    status: "unlocked",
    child: status,
    spoken: status.activeRoutineIsBedtime
      ? `${child.name} is unlocked, but bedtime is still scheduled.`
      : `${child.name} is unlocked.`,
  };
}

export interface LockAllResult {
  ok: boolean;
  results: ActionResult[];
  spoken: string;
}

/**
 * Lock everyone, sequentially.
 *
 * One child failing never fails the call: the per-child outcome is what the
 * PWA and the Shortcut surface.
 */
export async function lockAll(
  client: QustodioClient,
  store: Store,
  now: Date = client.now(),
): Promise<LockAllResult> {
  const results: ActionResult[] = [];

  for (const child of CHILDREN) {
    try {
      results.push(await lockChild(client, store, child, now));
    } catch (e) {
      const detail = e instanceof KitError ? e.detail : e instanceof Error ? e.message : String(e);
      const code = e instanceof KitError ? e.code : "error";
      results.push({
        ok: false,
        slug: child.slug,
        name: child.name,
        status: "error",
        detail: `${code}: ${detail}`,
        spoken: `Couldn't lock ${child.name}.`,
      });
    }
  }

  const failed = results.filter((r) => r.status === "error");
  const locked = results.filter((r) => r.status === "locked" || r.status === "already_locked");

  let spoken: string;
  if (failed.length === 0) {
    spoken = `Everyone is locked: ${locked.map((r) => r.name).join(", ")}.`;
  } else if (locked.length === 0) {
    spoken = `Couldn't lock anyone. Check the app.`;
  } else {
    spoken =
      `Locked ${locked.map((r) => r.name).join(" and ")}. ` +
      `Couldn't lock ${failed.map((r) => r.name).join(" and ")}. Check the app.`;
  }

  return { ok: failed.length === 0, results, spoken };
}

// ------------------------------------------------------------------ helpers

async function scheduleExists(
  client: QustodioClient,
  child: ChildConfig,
  scheduleUid: string,
): Promise<boolean> {
  const schedules: Schedule[] = await client.getSchedules(child.profileUid, child.lockRoutineUid);
  return schedules.some((s) => s.uid === scheduleUid && s.overrides === true);
}

/** Run `check` up to `attempts` times, waiting between tries. */
async function verify(
  client: QustodioClient,
  check: () => Promise<boolean>,
  attempts: number,
): Promise<boolean> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await client.sleep(VERIFY_DELAY_MS);
    if (await check()) return true;
  }
  return false;
}
