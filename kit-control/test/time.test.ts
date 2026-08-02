import { describe, expect, it } from "vitest";

import {
  hoursSinceWindowEnded,
  localDateAfterMinutes,
  localParts,
  localToInstant,
  lockWindow,
  toLocalClock,
  toLocalIso,
  weekdayOfLocalDate,
  windowContains,
  windowEnd,
  windowStart,
} from "../src/time.js";

/**
 * Edmonton is UTC-7 during MDT and UTC-6 during MST.
 * 2026 transitions: forward 2026-03-08 02:00 -> 03:00, back 2026-11-01 02:00 -> 01:00.
 */

describe("localParts", () => {
  it("reports Edmonton local time, not UTC", () => {
    // 2026-08-02T04:32:00Z is 2026-08-01 22:32 MDT — a different date locally.
    expect(localParts(new Date("2026-08-02T04:32:00Z"))).toEqual({
      date: "2026-08-01",
      time: "22:32",
      weekday: "SA",
    });
  });

  it("handles local midnight without rolling the date", () => {
    // 06:00Z in August is exactly 00:00 MDT.
    expect(localParts(new Date("2026-08-02T06:00:00Z"))).toEqual({
      date: "2026-08-02",
      time: "00:00",
      weekday: "SU",
    });
  });

  it("handles a month end that is a different month in UTC", () => {
    // 2026-09-01T02:00Z is 2026-08-31 20:00 MDT.
    expect(localParts(new Date("2026-09-01T02:00:00Z"))).toEqual({
      date: "2026-08-31",
      time: "20:00",
      weekday: "MO",
    });
  });

  it("handles a year end that is a different year in UTC", () => {
    // 2027-01-01T04:00Z is 2026-12-31 21:00 MST.
    expect(localParts(new Date("2027-01-01T04:00:00Z"))).toEqual({
      date: "2026-12-31",
      time: "21:00",
      weekday: "TH",
    });
  });

  it("reports MST in winter", () => {
    expect(localParts(new Date("2026-01-15T12:00:00Z"))).toEqual({
      date: "2026-01-15",
      time: "05:00",
      weekday: "TH",
    });
  });
});

describe("localToInstant", () => {
  it("converts a summer local time (MDT, UTC-6)", () => {
    // 22:10 MDT on 2026-08-01 is 04:10Z on 2026-08-02.
    expect(localToInstant("2026-08-01", "22:10").toISOString()).toBe("2026-08-02T04:10:00.000Z");
  });

  it("converts a winter local time (MST, UTC-7)", () => {
    expect(localToInstant("2026-01-15", "22:10").toISOString()).toBe("2026-01-16T05:10:00.000Z");
  });

  it("round-trips through localParts across the year", () => {
    for (const date of ["2026-01-15", "2026-03-07", "2026-06-21", "2026-10-31", "2026-12-31"]) {
      for (const time of ["00:00", "06:30", "13:45", "23:59"]) {
        expect(localParts(localToInstant(date, time))).toMatchObject({ date, time });
      }
    }
  });

  describe("spring forward, 2026-03-08", () => {
    it("keeps 01:59 local on the MST side", () => {
      expect(localToInstant("2026-03-08", "01:59").toISOString()).toBe("2026-03-08T08:59:00.000Z");
    });

    it("puts 03:00 local on the MDT side", () => {
      expect(localToInstant("2026-03-08", "03:00").toISOString()).toBe("2026-03-08T09:00:00.000Z");
    });

    it("resolves 02:30, which does not exist locally, to the instant just before the jump", () => {
      // The skipped hour cannot round-trip. It lands an hour earlier, still on
      // MST, which widens rather than narrows a lock window starting there.
      const instant = localToInstant("2026-03-08", "02:30");
      expect(instant.toISOString()).toBe("2026-03-08T08:30:00.000Z");
      expect(localParts(instant).time).toBe("01:30");
      expect(instant.getTime()).toBeLessThan(localToInstant("2026-03-08", "03:00").getTime());
    });

    it("spans only 23 hours of real time across the forward transition", () => {
      const start = localToInstant("2026-03-08", "00:00");
      const end = localToInstant("2026-03-09", "00:00");
      expect((end.getTime() - start.getTime()) / 3_600_000).toBe(23);
    });
  });

  describe("fall back, 2026-11-01", () => {
    it("keeps 00:59 local on the MDT side", () => {
      expect(localToInstant("2026-11-01", "00:59").toISOString()).toBe("2026-11-01T06:59:00.000Z");
    });

    it("resolves the repeated 01:30 to the first (MDT) occurrence", () => {
      const instant = localToInstant("2026-11-01", "01:30");
      expect(instant.toISOString()).toBe("2026-11-01T07:30:00.000Z");
      expect(localParts(instant).time).toBe("01:30");
    });

    it("puts 03:00 local on the MST side", () => {
      expect(localToInstant("2026-11-01", "03:00").toISOString()).toBe("2026-11-01T10:00:00.000Z");
    });

    it("spans 25 hours of real time across the back transition", () => {
      const start = localToInstant("2026-11-01", "00:00");
      const end = localToInstant("2026-11-02", "00:00");
      expect((end.getTime() - start.getTime()) / 3_600_000).toBe(25);
    });
  });
});

describe("weekdayOfLocalDate", () => {
  it("returns the two-letter weekday of the date as written", () => {
    expect(weekdayOfLocalDate("2026-08-01")).toBe("SA");
    expect(weekdayOfLocalDate("2026-08-02")).toBe("SU");
    expect(weekdayOfLocalDate("2026-03-08")).toBe("SU");
    expect(weekdayOfLocalDate("2026-12-31")).toBe("TH");
  });
});

describe("localDateAfterMinutes", () => {
  it("stays on the same local date inside one day", () => {
    expect(localDateAfterMinutes(localToInstant("2026-08-01", "08:00"), 480)).toBe("2026-08-01");
  });

  it("rolls to the next local date across midnight", () => {
    expect(localDateAfterMinutes(localToInstant("2026-08-01", "22:10"), 480)).toBe("2026-08-02");
  });

  it("rolls across a month end", () => {
    expect(localDateAfterMinutes(localToInstant("2026-08-31", "22:10"), 480)).toBe("2026-09-01");
  });

  it("rolls across a year end", () => {
    expect(localDateAfterMinutes(localToInstant("2026-12-31", "22:10"), 480)).toBe("2027-01-01");
  });
});

describe("lockWindow", () => {
  it("builds the body for an overnight lock", () => {
    // 2026-08-02T04:10:00Z is Saturday 22:10 local.
    expect(lockWindow(new Date("2026-08-02T04:10:00Z"), 480)).toEqual({
      weekdays: ["SA"],
      start_time: "22:10",
      from_date: "2026-08-01",
      to_date: "2026-08-02",
      duration_minutes: 480,
    });
  });

  it("sets to_date equal to from_date when the window stays inside one day", () => {
    const body = lockWindow(localToInstant("2026-08-01", "08:00"), 480);
    expect(body).toEqual({
      weekdays: ["SA"],
      start_time: "08:00",
      from_date: "2026-08-01",
      to_date: "2026-08-01",
      duration_minutes: 480,
    });
  });

  it("uses the local weekday of from_date, not the UTC one", () => {
    // Locally Friday 2026-07-31 23:30; in UTC it is already Saturday.
    const body = lockWindow(new Date("2026-08-01T05:30:00Z"), 480);
    expect(body.weekdays).toEqual(["FR"]);
    expect(body.from_date).toBe("2026-07-31");
    expect(weekdayOfLocalDate(body.from_date)).toBe(body.weekdays[0]);
  });

  it("emits start_time without seconds", () => {
    expect(lockWindow(new Date("2026-08-02T04:10:37Z"), 480).start_time).toMatch(/^\d{2}:\d{2}$/);
  });
});

describe("window containment", () => {
  const overnight = { from_date: "2026-08-01", start_time: "22:10", duration_minutes: 480 };

  it("contains 02:00 the following local day", () => {
    expect(windowContains(overnight, localToInstant("2026-08-02", "02:00"))).toBe(true);
  });

  it("is inclusive at the start", () => {
    expect(windowContains(overnight, localToInstant("2026-08-01", "22:10"))).toBe(true);
  });

  it("excludes the moment before it starts", () => {
    expect(windowContains(overnight, localToInstant("2026-08-01", "22:09"))).toBe(false);
  });

  it("is exclusive at the end (22:10 + 480 minutes = 06:10)", () => {
    expect(windowContains(overnight, localToInstant("2026-08-02", "06:10"))).toBe(false);
    expect(windowContains(overnight, localToInstant("2026-08-02", "06:09"))).toBe(true);
  });

  it("accepts a start_time that carries seconds, as the API returns it", () => {
    const withSeconds = { ...overnight, start_time: "22:10:00" };
    expect(windowStart(withSeconds).getTime()).toBe(windowStart(overnight).getTime());
    expect(windowContains(withSeconds, localToInstant("2026-08-02", "02:00"))).toBe(true);
  });

  it("measures duration in real elapsed minutes across the spring-forward night", () => {
    // 22:10 on 2026-03-07 + 480 real minutes. The clock jumps an hour, so the
    // window ends at 07:10 local rather than 06:10.
    const w = { from_date: "2026-03-07", start_time: "22:10", duration_minutes: 480 };
    expect(localParts(windowEnd(w)).time).toBe("07:10");
    expect(windowContains(w, localToInstant("2026-03-08", "06:00"))).toBe(true);
  });

  it("measures duration in real elapsed minutes across the fall-back night", () => {
    const w = { from_date: "2026-10-31", start_time: "22:10", duration_minutes: 480 };
    expect(localParts(windowEnd(w)).time).toBe("05:10");
    expect(windowContains(w, localToInstant("2026-11-01", "05:00"))).toBe(true);
  });
});

describe("hoursSinceWindowEnded", () => {
  const w = { from_date: "2026-08-01", start_time: "22:10", duration_minutes: 480 };

  it("is negative while the window is still running", () => {
    expect(hoursSinceWindowEnded(w, localToInstant("2026-08-02", "02:00"))).toBeLessThan(0);
  });

  it("counts hours since the window closed", () => {
    expect(hoursSinceWindowEnded(w, localToInstant("2026-08-02", "12:10"))).toBeCloseTo(6, 6);
  });

  it("passes 24 hours a full day after the window closed", () => {
    expect(hoursSinceWindowEnded(w, localToInstant("2026-08-03", "07:00"))).toBeGreaterThan(24);
    expect(hoursSinceWindowEnded(w, localToInstant("2026-08-03", "05:00"))).toBeLessThan(24);
  });
});

describe("display helpers", () => {
  it("formats an ISO stamp with the summer offset", () => {
    expect(toLocalIso(localToInstant("2026-08-02", "06:32"))).toBe("2026-08-02T06:32:00-06:00");
  });

  it("formats an ISO stamp with the winter offset", () => {
    expect(toLocalIso(localToInstant("2026-01-15", "06:32"))).toBe("2026-01-15T06:32:00-07:00");
  });

  it("formats a spoken clock time", () => {
    expect(toLocalClock(localToInstant("2026-08-02", "06:32"))).toBe("6:32 AM");
    expect(toLocalClock(localToInstant("2026-08-02", "18:05"))).toBe("6:05 PM");
    expect(toLocalClock(localToInstant("2026-08-02", "00:00"))).toBe("12:00 AM");
    expect(toLocalClock(localToInstant("2026-08-02", "12:00"))).toBe("12:00 PM");
  });
});
