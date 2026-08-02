/**
 * Every date and time sent to Qustodio is Edmonton local, never UTC. The
 * Worker's clock is UTC, so all conversion goes through Intl with an explicit
 * timeZone. Nothing in here reads the ambient zone.
 */

import { TIMEZONE } from "./config.js";

export type Weekday = "MO" | "TU" | "WE" | "TH" | "FR" | "SA" | "SU";

export interface LocalParts {
  /** YYYY-MM-DD in Edmonton local time. */
  date: string;
  /** HH:MM, no seconds — the format the schedules endpoint accepts. */
  time: string;
  /** Two-letter local weekday. */
  weekday: Weekday;
}

const WEEKDAY_BY_INDEX: readonly Weekday[] = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"];

const WEEKDAY_BY_SHORT_NAME: Readonly<Record<string, Weekday>> = {
  Sun: "SU",
  Mon: "MO",
  Tue: "TU",
  Wed: "WE",
  Thu: "TH",
  Fri: "FR",
  Sat: "SA",
};

const partsFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: TIMEZONE,
  hourCycle: "h23",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  weekday: "short",
});

interface RawParts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
  weekday: Weekday;
}

function rawParts(instant: Date, zone: string = TIMEZONE): RawParts {
  const formatter =
    zone === TIMEZONE
      ? partsFormatter
      : new Intl.DateTimeFormat("en-US", {
          timeZone: zone,
          hourCycle: "h23",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          weekday: "short",
        });

  const found: Record<string, string> = {};
  for (const part of formatter.formatToParts(instant)) {
    if (part.type !== "literal") found[part.type] = part.value;
  }

  const weekday = WEEKDAY_BY_SHORT_NAME[found.weekday ?? ""];
  if (!weekday) throw new Error(`Could not read weekday for ${instant.toISOString()}`);

  return {
    year: Number(found.year),
    month: Number(found.month),
    day: Number(found.day),
    // Some ICU builds emit hour 24 for midnight even under h23; normalise it.
    hour: Number(found.hour) % 24,
    minute: Number(found.minute),
    second: Number(found.second),
    weekday,
  };
}

const pad = (n: number, width = 2): string => String(n).padStart(width, "0");

/** Edmonton-local `{ date, time, weekday }` for an instant. */
export function localParts(instant: Date, zone: string = TIMEZONE): LocalParts {
  const p = rawParts(instant, zone);
  return {
    date: `${pad(p.year, 4)}-${pad(p.month)}-${pad(p.day)}`,
    time: `${pad(p.hour)}:${pad(p.minute)}`,
    weekday: p.weekday,
  };
}

/** Two-letter weekday for a `YYYY-MM-DD` local date string, without a zone round-trip. */
export function weekdayOfLocalDate(date: string): Weekday {
  const [y, m, d] = date.split("-").map(Number);
  if (y === undefined || m === undefined || d === undefined) {
    throw new Error(`Bad local date: ${date}`);
  }
  // Date.UTC + getUTCDay is a pure calendar lookup — no zone involved, so it is
  // the weekday of the local date as written.
  const index = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  const weekday = WEEKDAY_BY_INDEX[index];
  if (!weekday) throw new Error(`Bad local date: ${date}`);
  return weekday;
}

/**
 * Milliseconds that local time in `zone` runs ahead of UTC at `instant`.
 * Negative for Edmonton (UTC-7 in summer, UTC-6 in winter).
 */
function zoneOffsetMs(instant: Date, zone: string): number {
  const p = rawParts(instant, zone);
  const asIfUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  // Trim sub-second noise so the arithmetic is exact.
  return asIfUtc - instant.getTime() + (instant.getTime() % 1000);
}

/**
 * Convert an Edmonton-local wall-clock date and time to a real instant.
 *
 * Two passes: guess with the offset in effect at the naive timestamp, then
 * re-derive with the offset actually in effect at the guess. That second pass
 * is what makes the DST boundaries come out right.
 *
 * Boundary cases are deterministic:
 * - A local time skipped by spring-forward (02:30 on the transition date does
 *   not exist) resolves to the instant one hour before the nominal wall clock,
 *   just ahead of the jump. For a lock window that errs wider, which is the
 *   safer direction here.
 * - A local time repeated by fall-back resolves to the first occurrence, the
 *   one still on DST.
 */
export function localToInstant(date: string, time: string, zone: string = TIMEZONE): Date {
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  if (y === undefined || m === undefined || d === undefined) {
    throw new Error(`Bad local date: ${date}`);
  }
  if (hh === undefined || mm === undefined) {
    throw new Error(`Bad local time: ${time}`);
  }

  const naive = Date.UTC(y, m - 1, d, hh, mm, 0);
  let guess = new Date(naive - zoneOffsetMs(new Date(naive), zone));
  guess = new Date(naive - zoneOffsetMs(guess, zone));
  return guess;
}

/** Local date `YYYY-MM-DD` that `minutes` from `from` lands on. */
export function localDateAfterMinutes(from: Date, minutes: number, zone: string = TIMEZONE): string {
  return localParts(new Date(from.getTime() + minutes * 60_000), zone).date;
}

export interface OverrideWindow {
  from_date: string;
  start_time: string;
  duration_minutes: number;
}

/** Start instant of an override window. */
export function windowStart(w: OverrideWindow, zone: string = TIMEZONE): Date {
  // start_time comes back from Qustodio with seconds ("22:10:00") but is sent
  // without them. Accept both.
  const [hh, mm] = w.start_time.split(":");
  return localToInstant(w.from_date, `${hh}:${mm}`, zone);
}

/** End instant of an override window. */
export function windowEnd(w: OverrideWindow, zone: string = TIMEZONE): Date {
  return new Date(windowStart(w, zone).getTime() + w.duration_minutes * 60_000);
}

/** Whether `now` falls inside the window. Start inclusive, end exclusive. */
export function windowContains(w: OverrideWindow, now: Date, zone: string = TIMEZONE): boolean {
  const t = now.getTime();
  return t >= windowStart(w, zone).getTime() && t < windowEnd(w, zone).getTime();
}

/** How long ago the window ended, in hours. Negative while it is still running. */
export function hoursSinceWindowEnded(
  w: OverrideWindow,
  now: Date,
  zone: string = TIMEZONE,
): number {
  return (now.getTime() - windowEnd(w, zone).getTime()) / 3_600_000;
}

export interface LockWindowRequest {
  weekdays: [Weekday];
  start_time: string;
  from_date: string;
  to_date: string;
  duration_minutes: number;
}

/**
 * Body fields for a lock of `durationMinutes` starting now.
 *
 * `weekdays` is a single-element array holding the local weekday of
 * `from_date`. `to_date` is the local date the window ends on — equal to
 * `from_date` inside one day, a day later when it crosses midnight.
 */
export function lockWindow(
  now: Date,
  durationMinutes: number,
  zone: string = TIMEZONE,
): LockWindowRequest {
  const start = localParts(now, zone);
  return {
    weekdays: [start.weekday],
    start_time: start.time,
    from_date: start.date,
    to_date: localDateAfterMinutes(now, durationMinutes, zone),
    duration_minutes: durationMinutes,
  };
}

/** ISO 8601 with the Edmonton offset, e.g. `2026-08-02T06:32:00-06:00`. */
export function toLocalIso(instant: Date, zone: string = TIMEZONE): string {
  const p = rawParts(instant, zone);
  const offsetMinutes = Math.round(zoneOffsetMs(instant, zone) / 60_000);
  const sign = offsetMinutes < 0 ? "-" : "+";
  const abs = Math.abs(offsetMinutes);
  const stamp =
    `${pad(p.year, 4)}-${pad(p.month)}-${pad(p.day)}` +
    `T${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}`;
  return `${stamp}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** Speech- and label-friendly local clock time, e.g. `6:32 AM`. */
export function toLocalClock(instant: Date, zone: string = TIMEZONE): string {
  const p = rawParts(instant, zone);
  const suffix = p.hour < 12 ? "AM" : "PM";
  const hour12 = p.hour % 12 === 0 ? 12 : p.hour % 12;
  return `${hour12}:${pad(p.minute)} ${suffix}`;
}
