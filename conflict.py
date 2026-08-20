import os
from datetime import datetime, timedelta, timezone
from ticket_submission_log import parse_log_start_and_end


# A ticket filed this close to the session is not something the GN can act on, so
# filing it is noise rather than a request. These are held back like a conflict and
# reported to the person, who can still book by hand if it is genuinely worth it.
LAST_MINUTE_HOURS = int(os.getenv("GN_LAST_MINUTE_HOURS", "12"))


def check_for_time_conflicts(candidate_sessions, existing_sessions, historical_ticket_entries=None,
                             now=None, last_minute_hours=None):
    """
    Checks for conflicts between candidate sessions (new GN ticket requests)
    and previously existing Airtable sessions for the same school.

    Three scenarios are considered:
    1. Sessions starting too soon for the GN to act on the request.
    2. Time conflicts against already booked sessions that already have GN tickets.
    3. Candidate sessions that are booked (but no GN ticket yet) and overlap in time.
    """

    candidate_ids = {session.s_id for session in candidate_sessions}
    historical_ticket_entries = historical_ticket_entries or []

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hours = LAST_MINUTE_HOURS if last_minute_hours is None else last_minute_hours
    last_minute_cutoff = now + timedelta(hours=hours)

    for candidate in candidate_sessions:
        candidate.is_conflict = False
        candidate.conflict_details = ""
        candidate.conflict_type = None
        candidate.conflict_start_iso = None
        candidate.conflict_end_iso = None
        candidate.conflict_other_id = None
        candidate.conflict_other_title = None
        candidate.conflict_other_teacher = None
        candidate.conflict_other_teacher_email = None
        candidate.conflict_other_start_iso = None
        candidate.conflict_other_created_at = None

        candidate_start = candidate.start_time
        candidate_end = (candidate_start + timedelta(minutes=candidate.length or 0)
                         if candidate_start else None)

        # Checked before anything else: there is no point working out who to email
        # about a clash for a session that is already too close to file for.
        if candidate_start and hours > 0:
            start_utc = candidate_start
            if start_utc.tzinfo is None:
                start_utc = start_utc.replace(tzinfo=timezone.utc)
            if start_utc <= last_minute_cutoff:
                candidate.is_conflict = True
                candidate.conflict_type = "last_minute"
                candidate.conflict_details = (
                    f"Starts within {hours} hours, too late for the GN to action a "
                    f"request. Book it by hand if it still needs a ticket."
                )
                continue

        if candidate_start:
            for existing in existing_sessions:
                if existing.s_id in candidate_ids:
                    continue
                if candidate.school != existing.school:
                    continue
                if (existing.status or "").strip().lower() != "booked":
                    continue
                if not getattr(existing, 'gn_ticket_requested', False):
                    continue
                if not existing.start_time:
                    continue

                existing_start = existing.start_time
                existing_end = existing_start + timedelta(minutes=existing.length or 0)

                if candidate_start < existing_end and existing_start < candidate_end:
                    candidate.is_conflict = True
                    candidate.conflict_type = "time"
                    candidate.conflict_details = (
                        f"Conflicts with previously booked session '{existing.title}'."
                    )
                    candidate.conflict_start_iso = existing_start.isoformat()
                    candidate.conflict_end_iso = existing_end.isoformat()
                    candidate.conflict_other_id = existing.s_id
                    candidate.conflict_other_title = existing.title
                    candidate.conflict_other_teacher = existing.teacher
                    candidate.conflict_other_teacher_email = getattr(existing, 'teacher_email', '')
                    candidate.conflict_other_start_iso = existing_start.isoformat()
                    candidate.conflict_other_created_at = getattr(existing, 'created_at', '')
                    break

        if candidate.is_conflict:
            continue

        if candidate_start:
            for historical_entry in historical_ticket_entries:
                if historical_entry.get('session_id') == candidate.s_id:
                    continue

                historical_school = (historical_entry.get('school') or '').strip().lower()
                if historical_school != (candidate.school or '').strip().lower():
                    continue

                historical_start, historical_end = parse_log_start_and_end(historical_entry)
                if not historical_start:
                    continue

                if candidate_start < historical_end and historical_start < candidate_end:
                    candidate.is_conflict = True
                    candidate.conflict_type = "ghost_ticket"
                    ticket_id = historical_entry.get('ticket_id', 'Unknown')
                    candidate.conflict_details = (
                        f"Rebooked/ghost ticket conflict with submitted ticket {ticket_id} "
                        f"for '{historical_entry.get('title', 'Unknown Session')}'."
                    )
                    candidate.conflict_start_iso = historical_start.isoformat()
                    candidate.conflict_end_iso = historical_end.isoformat()
                    break

        if candidate.is_conflict:
            continue

    sessions_with_time = [
        session for session in candidate_sessions if session.start_time
    ]
    for index, candidate in enumerate(sessions_with_time):
        if candidate.is_conflict:
            continue
        if (candidate.status or "").strip().lower() != "booked":
            continue
        if getattr(candidate, 'gn_ticket_requested', False):
            continue

        candidate_start = candidate.start_time
        candidate_end = candidate_start + timedelta(minutes=candidate.length or 0)

        for other in sessions_with_time[index + 1:]:
            if other.is_conflict:
                continue
            if (other.status or "").strip().lower() != "booked":
                continue
            if getattr(other, 'gn_ticket_requested', False):
                continue

            other_start = other.start_time
            other_end = other_start + timedelta(minutes=other.length or 0)

            if candidate.school != other.school:
                continue

            if candidate_start < other_end and other_start < candidate_end:
                details = (
                    f"Conflicts with another booked session '{other.title}'."
                )

                candidate.is_conflict = True
                candidate.conflict_type = "time"
                candidate.conflict_details = details
                candidate.conflict_start_iso = other_start.isoformat()
                candidate.conflict_end_iso = other_end.isoformat()
                candidate.conflict_other_id = other.s_id
                candidate.conflict_other_title = other.title
                candidate.conflict_other_teacher = other.teacher
                candidate.conflict_other_teacher_email = getattr(other, 'teacher_email', '')
                candidate.conflict_other_start_iso = other_start.isoformat()
                candidate.conflict_other_created_at = getattr(other, 'created_at', '')

                if not other.is_conflict:
                    other.is_conflict = True
                    other.conflict_type = "time"
                    other.conflict_details = (
                        f"Conflicts with another booked session '{candidate.title}'."
                    )
                    other.conflict_start_iso = candidate_start.isoformat()
                    other.conflict_end_iso = candidate_end.isoformat()
                    other.conflict_other_id = candidate.s_id
                    other.conflict_other_title = candidate.title
                    other.conflict_other_teacher = candidate.teacher
                    other.conflict_other_teacher_email = getattr(candidate, 'teacher_email', '')
                    other.conflict_other_start_iso = candidate_start.isoformat()
                    other.conflict_other_created_at = getattr(candidate, 'created_at', '')
                break

    sessions_with_time = [
        session for session in candidate_sessions if session.start_time
    ]
    for index, candidate in enumerate(sessions_with_time):
        if candidate.is_conflict:
            continue

        candidate_start = candidate.start_time
        candidate_end = candidate_start + timedelta(minutes=candidate.length or 0)
        candidate_teacher = (candidate.teacher or "").strip().lower()
        if not candidate_teacher:
            continue

        for other in sessions_with_time[index + 1:]:
            if other.is_conflict:
                continue

            other_teacher = (other.teacher or "").strip().lower()
            if candidate_teacher != other_teacher:
                continue

            other_start = other.start_time
            other_end = other_start + timedelta(minutes=other.length or 0)

            if candidate_start < other_end and other_start < candidate_end:
                details = (
                    f"Conflicts with another in-progress session '{other.title}'."
                )

                candidate.is_conflict = True
                candidate.conflict_type = "time"
                candidate.conflict_details = details
                candidate.conflict_start_iso = other_start.isoformat()
                candidate.conflict_end_iso = other_end.isoformat()
                candidate.conflict_other_id = other.s_id
                candidate.conflict_other_title = other.title
                candidate.conflict_other_teacher = other.teacher
                candidate.conflict_other_teacher_email = getattr(other, 'teacher_email', '')
                candidate.conflict_other_start_iso = other_start.isoformat()
                candidate.conflict_other_created_at = getattr(other, 'created_at', '')

                if not other.is_conflict:
                    other.is_conflict = True
                    other.conflict_type = "time"
                    other.conflict_details = (
                        f"Conflicts with another in-progress session '{candidate.title}'."
                    )
                    other.conflict_start_iso = candidate_start.isoformat()
                    other.conflict_end_iso = candidate_end.isoformat()
                    other.conflict_other_id = candidate.s_id
                    other.conflict_other_title = candidate.title
                    other.conflict_other_teacher = candidate.teacher
                    other.conflict_other_teacher_email = getattr(candidate, 'teacher_email', '')
                    other.conflict_other_start_iso = candidate_start.isoformat()
                    other.conflict_other_created_at = getattr(candidate, 'created_at', '')
                break

    return candidate_sessions
