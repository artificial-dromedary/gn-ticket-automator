from datetime import timedelta
from ticket_submission_log import parse_log_start_and_end


def check_for_time_conflicts(candidate_sessions, existing_sessions, historical_ticket_entries=None):
    """
    Checks for conflicts between candidate sessions (new GN ticket requests)
    and previously existing Airtable sessions for the same school.

    Two scenarios are considered:
    1. Time conflicts against already booked sessions that already have GN tickets.
    2. Candidate sessions that are booked (but no GN ticket yet) and overlap in time.
    """

    candidate_ids = {session.s_id for session in candidate_sessions}
    historical_ticket_entries = historical_ticket_entries or []

    for candidate in candidate_sessions:
        candidate.is_conflict = False
        candidate.conflict_details = ""
        candidate.conflict_type = None
        candidate.conflict_start_iso = None
        candidate.conflict_end_iso = None

        candidate_start = candidate.start_time
        candidate_end = (candidate_start + timedelta(minutes=candidate.length or 0)
                         if candidate_start else None)

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

                if not other.is_conflict:
                    other.is_conflict = True
                    other.conflict_type = "time"
                    other.conflict_details = (
                        f"Conflicts with another booked session '{candidate.title}'."
                    )
                    other.conflict_start_iso = candidate_start.isoformat()
                    other.conflict_end_iso = candidate_end.isoformat()
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

                if not other.is_conflict:
                    other.is_conflict = True
                    other.conflict_type = "time"
                    other.conflict_details = (
                        f"Conflicts with another in-progress session '{candidate.title}'."
                    )
                    other.conflict_start_iso = candidate_start.isoformat()
                    other.conflict_end_iso = candidate_end.isoformat()
                break

    return candidate_sessions
