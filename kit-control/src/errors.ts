/**
 * Typed errors. These need different responses from the caller and different
 * words in the PWA, so they never collapse into "something went wrong".
 */

export type KitErrorCode =
  /** Login or refresh rejected. Password change or account lock. */
  | "auth_failed"
  /** Unexpected status, or a response missing expected fields. Qustodio shipped a change. */
  | "api_changed"
  /** The write was accepted but state did not change. Qustodio-side glitch. */
  | "verify_failed"
  /** Locking this child is disabled by a config prerequisite. */
  | "lock_disabled"
  /** Unknown child slug. */
  | "unknown_child"
  /** Missing or wrong bearer token. */
  | "unauthorized";

export class KitError extends Error {
  readonly code: KitErrorCode;
  readonly status: number;
  readonly detail: string;

  constructor(code: KitErrorCode, detail: string, status?: number) {
    super(`${code}: ${detail}`);
    this.name = "KitError";
    this.code = code;
    this.detail = detail;
    this.status = status ?? defaultStatus(code);
  }

  toJSON(): { error: KitErrorCode; detail: string } {
    return { error: this.code, detail: this.detail };
  }
}

function defaultStatus(code: KitErrorCode): number {
  switch (code) {
    case "auth_failed":
      return 502;
    case "api_changed":
      return 502;
    case "verify_failed":
      return 502;
    case "lock_disabled":
      // A precondition outside this request has not been met.
      return 412;
    case "unknown_child":
      return 404;
    case "unauthorized":
      return 401;
  }
}

export function isKitError(e: unknown): e is KitError {
  return e instanceof KitError;
}

/** Wrap anything thrown into a KitError so route handlers have one shape to render. */
export function asKitError(e: unknown): KitError {
  if (isKitError(e)) return e;
  const detail = e instanceof Error ? e.message : String(e);
  return new KitError("api_changed", detail);
}
