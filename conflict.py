import os
from datetime import datetime, timedelta, timezone
from ticket_submission_log import parse_log_start_and_end


# A ticket filed this close to the session is not something the GN can act on, so
# filing it is noise rather than a request. These are held back like a conflict and
# reported to the person, who can still book by hand if it is genuinely worth it.
LAST_MINUTE_HOURS = int(os.getenv("GN_LAST_MINUTE_HOURS", "12"))

# Every conflict attribute a session carries, with its cleared value. This is the
# one place the set is defined: reset_conflict() writes these, mark_conflict()
# fills them in, and conflict_payload() reads them back out. The stored scan
# JSON and the emails use these same names, so renaming one is a data change.
CONFLICT_DEFAULTS = {
    "is_conflict": False,
    "conflict_type": None,
    "conflict_details": "",
    "conflict_start_iso": None,
    "conflict_end_iso": None,
    # The other session in a time clash. Everything the email to the school needs.
    "conflict_other_id": None,
    "conflict_other_title": None,
    "conflict_other_teacher": None,
    "conflict_other_teachers": [],
    "conflict_other_teacher_email": None,
    "conflict_other_teacher_emails": [],
    "conflict_other_start_iso": None,
    "conflict_other_created_at": None,
    # True when the other session already has a GN ticket, so it holds the Cisco
    # machine no matter which of the two was created first.
    "conflict_other_ticketed": False,
}


def reset_conflict(session):
    for name, default in CONFLICT_DEFAULTS.items():
        setattr(session, name, list(default) if isinstance(default, list) else default)


def _window(start, length):
    return start, start + timedelta(minutes=length or 0)


def mark_conflict(session, kind, details, start=None, end=None, other=None, other_ticketed=False):
    """Flag `session` as held back, recording the window it clashes with and, for a
    time clash, everything about the other session the notice to the school needs."""
    session.is_conflict = True
    session.conflict_type = kind
    session.conflict_details = details
    session.conflict_start_iso = start.isoformat() if start else None
    session.conflict_end_iso = end.isoformat() if end else None
    if other is not None:
        session.conflict_other_id = other.s_id
        session.conflict_other_title = other.title
        session.conflict_other_teacher = other.teacher
        session.conflict_other_teachers = list(getattr(other, "teachers", []) or [])
        session.conflict_other_teacher_email = getattr(other, "teacher_email", "")
        session.conflict_other_teacher_emails = list(getattr(other, "teacher_emails", []) or [])
        session.conflict_other_start_iso = other.start_time.isoformat() if other.start_time else None
        session.conflict_other_created_at = getattr(other, "created_at", "")
        session.conflict_other_ticketed = other_ticketed


def _mark_pair(a, b, wording):
    """Both sides of a clash between two candidates, each pointing at the other."""
    a_start, a_end = _window(a.start_time, a.length)
    b_start, b_end = _window(b.start_time, b.length)
    mark_conflict(a, "time", wording.format(title=b.title), b_start, b_end, other=b)
    if not b.is_conflict:
        mark_conflict(b, "time", wording.format(title=a.title), a_start, a_end, other=a)


def conflict_payload(session):
    """The session as the scan stores and emails it: a plain dict, JSON-safe."""
    payload = {
        "session_id": session.s_id,
        "title": session.title,
        "school": session.school,
        "teacher": session.teacher,
        "teachers": list(getattr(session, "teachers", []) or []),
        "teacher_email": getattr(session, "teacher_email", ""),
        "teacher_emails": list(getattr(session, "teacher_emails", []) or []),
        "start_time": session.start_time.isoformat() if session.start_time else None,
        "length": session.length,
        "timezone": getattr(session, "timezone", ""),
        "created_at": getattr(session, "created_at", ""),
    }
    for name, default in CONFLICT_DEFAULTS.items():
        if name == "is_conflict":
            continue
        payload[name] = getattr(session, name, default)
    # The stored name for the other session's id predates the attribute name.
    payload["conflict_session_id"] = payload.pop("conflict_other_id")
    return payload


def _is_booked(session):
    return (session.status or "").strip().lower() == "booked"


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def check_for_time_conflicts(candidate_sessions, existing_sessions, historical_ticket_entries=None,
                             now=None, last_minute_hours=None):
    """Flag every candidate that should not be ticketed as it stands.

    In order of precedence:
    1. Starting too soon for the GN to act on the request.
    2. Overlapping a booked session at the same school that already has a ticket.
    3. Overlapping a ticket this tool already submitted for that school (a session
       that was rebooked after its ticket went in).
    4. Overlapping another candidate at the same school, or with the same teacher.
    """
    candidate_ids = {session.s_id for session in candidate_sessions}
    historical_ticket_entries = historical_ticket_entries or []

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hours = LAST_MINUTE_HOURS if last_minute_hours is None else last_minute_hours
    last_minute_cutoff = now + timedelta(hours=hours)

    for candidate in candidate_sessions:
        reset_conflict(candidate)
        if not candidate.start_time:
            continue
        candidate_start, candidate_end = _window(candidate.start_time, candidate.length)

        # Checked before anything else: there is no point working out who to email
        # about a clash for a session that is already too close to file for.
        if hours > 0:
            start_utc = candidate_start
            if start_utc.tzinfo is None:
                start_utc = start_utc.replace(tzinfo=timezone.utc)
            if start_utc <= last_minute_cutoff:
                mark_conflict(candidate, "last_minute",
                              f"Starts within {hours} hours, too late for the GN to action a "
                              f"request. Book it by hand if it still needs a ticket.")
                continue

        for existing in existing_sessions:
            if existing.s_id in candidate_ids or candidate.school != existing.school:
                continue
            if not _is_booked(existing) or not getattr(existing, "gn_ticket_requested", False):
                continue
            if not existing.start_time:
                continue
            existing_start, existing_end = _window(existing.start_time, existing.length)
            if _overlaps(candidate_start, candidate_end, existing_start, existing_end):
                mark_conflict(candidate, "time",
                              f"Conflicts with previously booked session '{existing.title}'.",
                              existing_start, existing_end, other=existing, other_ticketed=True)
                break
        if candidate.is_conflict:
            continue

        for entry in historical_ticket_entries:
            if entry.get("session_id") == candidate.s_id:
                continue
            if (entry.get("school") or "").strip().lower() != (candidate.school or "").strip().lower():
                continue
            historical_start, historical_end = parse_log_start_and_end(entry)
            if not historical_start:
                continue
            if _overlaps(candidate_start, candidate_end, historical_start, historical_end):
                ticket_id = entry.get("ticket_id", "Unknown")
                mark_conflict(candidate, "ghost_ticket",
                              f"Rebooked/ghost ticket conflict with submitted ticket {ticket_id} "
                              f"for '{entry.get('title', 'Unknown Session')}'.",
                              historical_start, historical_end)
                break

    timed = [session for session in candidate_sessions if session.start_time]

    # Two candidates at the same school wanting the Cisco machine at once.
    for index, candidate in enumerate(timed):
        if candidate.is_conflict or not _is_booked(candidate):
            continue
        if getattr(candidate, "gn_ticket_requested", False):
            continue
        candidate_start, candidate_end = _window(candidate.start_time, candidate.length)
        for other in timed[index + 1:]:
            if other.is_conflict or not _is_booked(other) or candidate.school != other.school:
                continue
            if getattr(other, "gn_ticket_requested", False):
                continue
            other_start, other_end = _window(other.start_time, other.length)
            if _overlaps(candidate_start, candidate_end, other_start, other_end):
                _mark_pair(candidate, other, "Conflicts with another booked session '{title}'.")
                break

    # The same teacher booked into two places at once.
    for index, candidate in enumerate(timed):
        if candidate.is_conflict:
            continue
        candidate_teacher = (candidate.teacher or "").strip().lower()
        if not candidate_teacher:
            continue
        candidate_start, candidate_end = _window(candidate.start_time, candidate.length)
        for other in timed[index + 1:]:
            if other.is_conflict or (other.teacher or "").strip().lower() != candidate_teacher:
                continue
            other_start, other_end = _window(other.start_time, other.length)
            if _overlaps(candidate_start, candidate_end, other_start, other_end):
                _mark_pair(candidate, other, "Conflicts with another in-progress session '{title}'.")
                break

    return candidate_sessions
