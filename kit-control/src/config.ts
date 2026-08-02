/**
 * Family map. Hard-coded on purpose: it changes rarely, and putting it in KV
 * would add a read to every request for no benefit.
 *
 * Nothing secret lives here. Profile and routine uids are opaque identifiers
 * that are useless without the account credentials, which are Worker secrets.
 */

export const ACCOUNT_ID = 8490996; // used in /v1/ paths
export const ACCOUNT_UID = "7905d68f66c94470acda2218c7cc7629"; // used in /v2/ paths
export const TIMEZONE = "America/Edmonton";
export const LOCK_DURATION_MINUTES = 480;

/** Qustodio hosts. */
export const AUTH_BASE = "https://auth.qustodio.com";
export const API_BASE = "https://api.qustodio.com";
export const REDIRECT_URI = "https://family.qustodio.com/parents-app";
export const ORIGIN = "https://family.qustodio.com";
export const USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36";

/** Status cache lifetime. Section 5 caps polling at 15 minutes. */
export const STATUS_CACHE_TTL_SECONDS = 15 * 60;

/** Stale `overrides: true` schedules are cleaned up once their window ended this long ago. */
export const STALE_OVERRIDE_GRACE_HOURS = 24;

export interface ChildConfig {
  /** URL slug, used in /api/lock/:child. */
  slug: string;
  name: string;
  profileId: number;
  profileUid: string;
  /**
   * The routine an override is written into to lock the child.
   *
   * This is "Do Chores" for all three. Coen's and Selah's Do Chores are
   * `block_type: 2` — the same full device block as Bedtime — so overriding
   * into it during a scheduled bedtime is not a downgrade. That is what makes
   * a single fixed lock duration correct and removes any need for
   * bedtime-aware duration logic.
   */
  lockRoutineUid: string;
  /**
   * Bedtime routine uid. Stored so status can label a scheduled bedtime
   * distinctly. The app never writes to it.
   */
  bedtimeRoutineUid: string;
  /**
   * Non-null when locking this child is disabled, with the reason. Set for a
   * child whose lock routine would not actually block the device, where
   * "locking" them would be materially weaker than locking the others — and,
   * during a scheduled bedtime, would reduce restrictions rather than
   * increase them.
   *
   * Lock and lock-all refuse the child while this is set; status and unlock
   * keep working. Clear it once the prerequisite is confirmed done in the
   * Qustodio app.
   */
  lockBlockedReason: string | null;
}

export const CHILDREN: readonly ChildConfig[] = [
  {
    slug: "taysha",
    name: "Taysha",
    profileId: 9555707,
    profileUid: "5b9c20ecafde40769efb10dc5390557a",
    // "Do Chores", not "Chore time". Taysha has both, holding overlapping
    // schedules over the same weekday hours, and which one wins is undefined.
    // "Do Chores" is the one this app writes to, matching her siblings.
    lockRoutineUid: "f7d0dabc27324d278d496d78acec8954",
    bedtimeRoutineUid: "cfc81a01aa664d33abe033a28aa08355",
    lockBlockedReason:
      "Taysha's Do Chores routine is block_type 0, not block_type 2. Locking her " +
      "would not block the device, and during a scheduled bedtime it would reduce " +
      "restrictions instead of increasing them. Set Do Chores to block devices " +
      "completely in the Qustodio app, then clear lockBlockedReason in config.ts.",
  },
  {
    slug: "selah",
    name: "Selah",
    profileId: 9669729,
    profileUid: "2fcb036a82e749149ac55f729eaa4852",
    lockRoutineUid: "997ccb15fa714d188b713558f95faa90",
    bedtimeRoutineUid: "16abf2829c524103981b43f567540d46",
    lockBlockedReason: null,
  },
  {
    slug: "coen",
    name: "Coen",
    profileId: 10068418,
    profileUid: "7f1fea574df04bde90e132692c515869",
    lockRoutineUid: "8754e28d8bf64cab9622a2dd9a72bb50",
    bedtimeRoutineUid: "0b09b00fb21946d0a33b1f738932411b",
    lockBlockedReason: null,
  },
] as const;

export function childBySlug(slug: string): ChildConfig | undefined {
  return CHILDREN.find((c) => c.slug === slug.toLowerCase());
}

/**
 * Routine uids that are known but never written to. Display names are not
 * hard-coded: names change, uids do not, so status fetches the routines list
 * and builds a uid to name map at read time. These are here as a record of
 * what was captured, and to let status fall back to a label if a routines
 * fetch comes back thin.
 *
 * Taysha's "Chore time" is the duplicate of "Do Chores" noted above. It is
 * listed here so it is unmistakably not the lock target.
 */
export const KNOWN_ROUTINE_UIDS: Readonly<Record<string, string>> = {
  "8ad5c7c0ceec47e1bdf8aefa99ef6d55": "Chore time (Taysha, duplicate — not the lock target)",
  f4fba31e1bb4418b84f850629081425e: "Drawing (Taysha)",
  "17b3e37c9cb140aabd67c4caa60c1d1c": "Play time (Taysha)",
  "273af932c8114d8c8c377ea7a1150ead": "Cricut (Selah)",
  c26069d83c784a2c8102cbbc2583eb16: "Play time (Selah)",
  "71a91ab4510c4ed6acf747107b7aa5cc": "Drawing (Coen)",
  "887074a24909420cab5ba647610052d6": "Play time (Coen)",
};
