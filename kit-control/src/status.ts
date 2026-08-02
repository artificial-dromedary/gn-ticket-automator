/**
 * Status.
 *
 * A child counts as locked when, and only when, a schedule with
 * `overrides: true` is currently inside its window. `active_routine` is read
 * for display and never decides `locked` — it cannot distinguish a routine
 * that happens to be running from one this app forced on, and it is null often
 * enough that keying off it would report a locked child as free.
 */

import {
  CHILDREN,
  KNOWN_ROUTINE_UIDS,
  STALE_OVERRIDE_GRACE_HOURS,
  STATUS_CACHE_TTL_SECONDS,
  type ChildConfig,
} from "./config.js";
import { QustodioClient, type Profile, type Routine, type Schedule } from "./qustodio.js";
import type { Store } from "./store.js";
import { hoursSinceWindowEnded, toLocalIso, windowContains, windowEnd } from "./time.js";

export type LockSource = "kit" | "qustodio";

export interface ChildStatus {
  slug: string;
  name: string;
  locked: boolean;
  /** ISO 8601 with the Edmonton offset, or null when not locked. */
  lockedUntil: string | null;
  /** Display name of `active_routine`, or null. */
  activeRoutine: string | null;
  /** True when the running routine is the child's scheduled bedtime. */
  activeRoutineIsBedtime: boolean;
  /** How the lock got there. null when not locked. */
  source: LockSource | null;
  /** False when a config prerequisite blocks locking this child. */
  lockAvailable: boolean;
  lockBlockedReason: string | null;
}

export interface StatusPayload {
  fetchedAt: string;
  children: ChildStatus[];
}

/** Everything read for one child in one pass. */
export interface ChildState {
  child: ChildConfig;
  profile: Profile | null;
  routineNames: Map<string, string>;
  /** Every `overrides: true` schedule found, with the routine it belongs to. */
  overrides: Array<{ routineUid: string; schedule: Schedule }>;
  /** The override currently inside its window, if any. */
  active: { routineUid: string; schedule: Schedule } | null;
}

function routineNameMap(routines: Routine[]): Map<string, string> {
  // Names are fetched rather than hard-coded: names change, uids do not.
  const map = new Map<string, string>();
  for (const routine of routines) map.set(routine.uid, routine.name);
  return map;
}

/**
 * Read one child's routines and schedules.
 *
 * Every routine is scanned, not just the lock routine, because an override set
 * from the Qustodio app can live on any of them.
 */
export async function readChildState(
  client: QustodioClient,
  child: ChildConfig,
  profiles: Profile[] | null = null,
  now: Date = client.now(),
): Promise<ChildState> {
  const profile =
    (profiles ?? (await client.getProfiles())).find((p) => p.uid === child.profileUid) ?? null;

  const routines = await client.getRoutines(child.profileUid);
  const overrides: Array<{ routineUid: string; schedule: Schedule }> = [];

  for (const routine of routines) {
    const schedules = await client.getSchedules(child.profileUid, routine.uid);
    for (const schedule of schedules) {
      if (schedule.overrides === true) {
        overrides.push({ routineUid: routine.uid, schedule });
      }
    }
  }

  const active = overrides.find((o) => windowContains(o.schedule, now)) ?? null;

  return {
    child,
    profile,
    routineNames: routineNameMap(routines),
    overrides,
    active,
  };
}

/**
 * Delete overrides whose window closed more than the grace period ago.
 *
 * Expired overrides linger in the schedules list — one from a previous session
 * was still present hours later. Only ever called with schedules already
 * confirmed `overrides: true`; the client asserts that again before deleting.
 */
export async function cleanUpStaleOverrides(
  client: QustodioClient,
  state: ChildState,
  now: Date,
  log: (message: string, data?: unknown) => void = () => {},
): Promise<number> {
  let removed = 0;

  for (const { routineUid, schedule } of state.overrides) {
    if (schedule.overrides !== true) continue; // belt and braces
    if (state.active?.schedule.uid === schedule.uid) continue;
    if (hoursSinceWindowEnded(schedule, now) <= STALE_OVERRIDE_GRACE_HOURS) continue;

    try {
      await client.deleteOverride(state.child.profileUid, routineUid, schedule);
      removed += 1;
    } catch (e) {
      // Cleanup is housekeeping. A failure here must not fail a status read.
      log("stale override cleanup failed", {
        child: state.child.name,
        schedule: schedule.uid,
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  }

  return removed;
}

function resolveRoutineName(state: ChildState, uid: string | null): string | null {
  if (!uid) return null;
  return state.routineNames.get(uid) ?? KNOWN_ROUTINE_UIDS[uid] ?? uid;
}

export async function childStatusFrom(
  state: ChildState,
  store: Store,
  now: Date,
): Promise<ChildStatus> {
  const { child, active } = state;

  let source: LockSource | null = null;
  if (active) {
    const record = await store.getOverride(child.profileUid);
    // "kit" only when KV names this exact schedule. An override this app did
    // not create — set from the Qustodio app — reads as "qustodio".
    source = record?.scheduleUid === active.schedule.uid ? "kit" : "qustodio";
  }

  const activeRoutineUid = state.profile?.active_routine ?? null;

  return {
    slug: child.slug,
    name: child.name,
    locked: active !== null,
    lockedUntil: active ? toLocalIso(windowEnd(active.schedule)) : null,
    activeRoutine: resolveRoutineName(state, activeRoutineUid),
    activeRoutineIsBedtime: activeRoutineUid === child.bedtimeRoutineUid,
    source,
    lockAvailable: child.lockBlockedReason === null,
    lockBlockedReason: child.lockBlockedReason,
  };
}

export interface ReadStatusOptions {
  now?: Date;
  log?: (message: string, data?: unknown) => void;
  /** Skip stale cleanup. Used by the verify reads, which must not write. */
  cleanUp?: boolean;
}

/** Full status for every child. Roughly 16 requests; not for every page focus. */
export async function readStatus(
  client: QustodioClient,
  store: Store,
  options: ReadStatusOptions = {},
): Promise<StatusPayload> {
  const now = options.now ?? client.now();
  const log = options.log ?? (() => {});
  const cleanUp = options.cleanUp ?? true;

  const profiles = await client.getProfiles();
  const children: ChildStatus[] = [];

  for (const child of CHILDREN) {
    const state = await readChildState(client, child, profiles, now);
    if (cleanUp) await cleanUpStaleOverrides(client, state, now, log);
    children.push(await childStatusFrom(state, store, now));
  }

  return { fetchedAt: toLocalIso(now), children };
}

/** Cached status, or a fresh read when the cache is empty or bypassed. */
export async function getStatus(
  client: QustodioClient,
  store: Store,
  options: ReadStatusOptions & { fresh?: boolean } = {},
): Promise<StatusPayload> {
  if (!options.fresh) {
    const cached = await store.getCachedStatus<StatusPayload>();
    if (cached) return cached;
  }

  const payload = await readStatus(client, store, options);
  await store.putCachedStatus(payload, STATUS_CACHE_TTL_SECONDS);
  return payload;
}

/** Drop the cached status so the next read reflects a write that just happened. */
export async function invalidateStatusCache(store: Store): Promise<void> {
  await store.putCachedStatus(null, 60);
}
