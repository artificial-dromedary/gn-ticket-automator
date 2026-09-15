import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Timestamps are stored as naive UTC but read by people in one place, so anything
# shown on a page or in an email is rendered in this zone. Deliberately its own
# setting rather than AUTO_SCAN_TZ: that one says when the scheduled scan fires,
# and the two answer different questions. Everyone reading this today is in
# Edmonton; when that stops being true this becomes a per-user preference.
DISPLAY_TZ = os.getenv("DISPLAY_TZ", "America/Edmonton")


def display_timezone_label():
    """Name the display zone as a person would: 'Edmonton' from 'America/Edmonton'."""
    return DISPLAY_TZ.rsplit("/", 1)[-1].replace("_", " ")


def friendly_datetime(value, fallback="unknown", tz=None):
    """Render a timestamp as 'Wed, Aug 19 at 8:36 pm MDT' in the display timezone.

    Accepts an ISO string or a datetime. A naive value is taken as UTC, which is how
    every timestamp in this app is stored. The zone abbreviation is always shown, so
    a time is never ambiguous to a reader somewhere else. Pass `tz` to render in a
    zone other than the default display one.
    """
    if not value:
        return fallback

    moment = value
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(moment, datetime):
        return fallback

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    try:
        local = moment.astimezone(ZoneInfo(tz or DISPLAY_TZ))
    except ZoneInfoNotFoundError:
        local = moment.astimezone(timezone.utc)

    # %-d / %-I are not portable to Windows, so strip the padding by hand.
    day = str(local.day)
    hour = str((local.hour % 12) or 12)
    meridiem = "am" if local.hour < 12 else "pm"
    zone = local.tzname() or ""
    return f"{local:%a}, {local:%b} {day} at {hour}:{local:%M} {meridiem} {zone}".strip()


def school_datetime(value, tz=None, fallback="unknown"):
    """Render a session time as 'Thursday, June 4 at 10:00 AM EDT' for a school.

    Used in wording that goes to teachers, so it reads in the school's own zone
    when Airtable has one and falls back to the display zone otherwise.
    """
    if not value:
        return fallback

    moment = value
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(moment, datetime):
        return fallback
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    try:
        local = moment.astimezone(ZoneInfo(tz or DISPLAY_TZ))
    except (ZoneInfoNotFoundError, ValueError):
        local = moment.astimezone(ZoneInfo(DISPLAY_TZ))

    hour = str((local.hour % 12) or 12)
    meridiem = "AM" if local.hour < 12 else "PM"
    zone = local.tzname() or ""
    return f"{local:%A}, {local:%B} {local.day} at {hour}:{local:%M} {meridiem} {zone}".strip()


def _field(session, name, default=None):
    """Read a field from a scan payload dict or a live session object alike."""
    if isinstance(session, dict):
        return session.get(name, default)
    return getattr(session, name, default)


def _conflict_pair(session):
    """Both sides of a time clash, in start order, plus which one keeps the machine.

    Only a time clash with a known other session counts; a last-minute hold or a
    ghost ticket is not something to put to the teachers. The session that keeps
    the Cisco machine is the one that already has a GN ticket, otherwise whichever
    was booked (created in Airtable) first. Returns None when there is no pair.
    """
    if _field(session, "conflict_type") != "time":
        return None
    other_start = _field(session, "conflict_other_start_iso")
    if not (_field(session, "conflict_session_id") or _field(session, "conflict_other_id")) or not other_start:
        return None

    start = _field(session, "start_time")
    if isinstance(start, datetime):
        start = start.isoformat()

    this = {
        "title": _field(session, "title") or "Session",
        "teacher": _field(session, "teacher") or "the teacher",
        "email": _field(session, "teacher_email") or "",
        "start": start,
        "created": _field(session, "created_at") or "",
    }
    other = {
        "title": _field(session, "conflict_other_title") or "Session",
        "teacher": _field(session, "conflict_other_teacher") or "the other teacher",
        "email": _field(session, "conflict_other_teacher_email") or "",
        "start": other_start,
        "created": _field(session, "conflict_other_created_at") or "",
    }

    other_first = (_field(session, "conflict_other_ticketed")
                   or not (this["created"] and other["created"])
                   or other["created"] <= this["created"])
    previous, current = (other, this) if other_first else (this, other)
    by_time = sorted([this, other], key=lambda s: s["start"] or "")
    return by_time, previous, current


def _first_name(full_name):
    return full_name.split()[0] if full_name.split() else full_name


def teacher_conflict_email(session):
    """The copy/paste email to a school about two overlapping sessions, or None."""
    pair = _conflict_pair(session)
    if not pair:
        return None
    by_time, previous, current = pair
    tz = _field(session, "timezone") or None

    bullets = "\n\n".join(
        f"\u2022 {s['title']} ({s['teacher']}): {school_datetime(s['start'], tz)}" for s in by_time
    )
    return (
        "Hi there,\n\n"
        "In setting up the videoconference connection, I saw that there are two "
        "sessions overlapping for your school:\n\n"
        f"{bullets}\n\n"
        "If your internet is pretty reliable/fast, one teacher can connect from the "
        "classroom via Zoom (rather than the Cisco machine). Otherwise, "
        f"{_first_name(previous['teacher'])}'s session will connect on the Cisco machine as it is "
        f"already set up, and we can rebook {_first_name(current['teacher'])}'s session.\n\n"
        "Could you let me know what you'd like to do?"
    )


def _append_teacher_emails(lines, conflict_sessions):
    """Under the conflicts: for each overlapping pair, who to write to, then what to say."""
    drafts = []
    seen = set()
    for session in conflict_sessions:
        key = frozenset(filter(None, [session.get("session_id"), session.get("conflict_session_id")]))
        if key in seen:
            continue
        draft = teacher_conflict_email(session)
        if draft:
            seen.add(key)
            drafts.append((session, _conflict_pair(session)[0], draft))

    if not drafts:
        return

    divider = "-" * 60
    lines.append("")
    lines.append(f"DRAFT EMAIL{'S' if len(drafts) > 1 else ''} TO TEACHERS ({len(drafts)})")
    for session, teachers, draft in drafts:
        lines.append("")
        lines.append(f"School: {session.get('school', 'Unknown')}")
        for teacher in teachers:
            lines.append(f"  {teacher['teacher']}: {teacher['email']}")
        lines.append("")
        lines.append("Copy and paste the text between the lines:")
        lines.append(divider)
        lines.append(draft)
        lines.append(divider)


def _send(to_email, subject, body):
    """Send a plain-text email using the configured SMTP relay."""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)

    if not smtp_user or not smtp_pass or not smtp_from:
        raise RuntimeError("SMTP_USER/SMTP_PASS/SMTP_FROM must be configured for email alerts.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)


def send_conflict_email(to_email, conflict_sessions, subject_prefix="GN Ticket Auto-Booking"):
    if not conflict_sessions:
        return

    lines = [
        "The automatic GN Ticket scan found conflicts. These sessions were NOT booked:",
        "",
    ]

    for session in conflict_sessions:
        lines.append(f"- {session.get('title', 'Unknown')} | {session.get('school', 'Unknown')} | {friendly_datetime(session.get('start_time'))}")
        if session.get('conflict_details'):
            lines.append(f"  Reason: {session.get('conflict_details')}")
        if session.get('conflict_start_iso') and session.get('conflict_end_iso'):
            lines.append(f"  Conflict window: {friendly_datetime(session.get('conflict_start_iso'))} – {friendly_datetime(session.get('conflict_end_iso'))}")
        lines.append("")

    lines.append("Resolve these in Airtable, or book them by hand from the dashboard.")
    _append_teacher_emails(lines, conflict_sessions)

    _send(to_email, f"{subject_prefix}: Conflicts Found", "\n".join(lines))


def send_booking_summary_email(to_email, successful_sessions, failed_sessions,
                               conflict_sessions=None, manual=False,
                               subject_prefix="GN Ticket Auto-Booking"):
    """Report what a booking run submitted, what failed, and what it would not touch.

    A manual run always sends, even when there was nothing to do: the dashboard
    promised an email when the person pressed the button, so silence would read as
    the run having got stuck.
    """
    successful_sessions = successful_sessions or []
    failed_sessions = failed_sessions or []
    conflict_sessions = conflict_sessions or []

    if not (successful_sessions or failed_sessions or conflict_sessions) and not manual:
        return

    lines = []
    if manual:
        lines.append("The booking run you started from the dashboard has finished.")
        lines.append("")

    if successful_sessions:
        lines.append(f"Booked {len(successful_sessions)} session(s) with GN:")
        lines.append("")
        for session in successful_sessions:
            lines.append(f"- {session.get('title', 'Unknown Session')} | {session.get('school', 'Unknown School')} | {friendly_datetime(session.get('start_time'))}")
            lines.append(f"  Ticket: {session.get('ticket_id', 'Unknown')}")
        lines.append("")
    elif manual:
        lines.append("No sessions were booked.")
        lines.append("")

    if failed_sessions:
        lines.append(f"{len(failed_sessions)} session(s) could not be booked:")
        lines.append("")
        for session in failed_sessions:
            lines.append(f"- {session.get('title', 'Unknown Session')}")
            lines.append(f"  Error: {session.get('error', 'Unknown error')}")
        lines.append("")
        lines.append("These will be retried on the next scheduled run.")
        lines.append("")

    if conflict_sessions:
        lines.append(f"{len(conflict_sessions)} session(s) were skipped because they conflict:")
        lines.append("")
        for session in conflict_sessions:
            lines.append(f"- {session.get('title', 'Unknown')} | {session.get('school', 'Unknown')} | {friendly_datetime(session.get('start_time'))}")
            if session.get('conflict_details'):
                lines.append(f"  Reason: {session.get('conflict_details')}")
        lines.append("")
        lines.append("Resolve these in Airtable, or book them by hand from the dashboard.")
        _append_teacher_emails(lines, conflict_sessions)

    subject = f"{subject_prefix}: Booked {len(successful_sessions)} session(s)"
    if failed_sessions:
        subject += f", {len(failed_sessions)} failed"
    if conflict_sessions:
        subject += f", {len(conflict_sessions)} conflicted"

    _send(to_email, subject, "\n".join(lines))


def send_daily_summary_email(to_email, booked_sessions, conflict_sessions, summary_date,
                             excluded_sessions=None, subject_prefix="GN Ticket Auto-Booking"):
    """The end-of-day picture: what was filed today, then what still needs a person.

    Sent once a day rather than after every run, so a short booking interval does
    not turn into a stream of near-identical mail.
    """
    booked_sessions = booked_sessions or []
    conflict_sessions = conflict_sessions or []
    excluded_sessions = excluded_sessions or []

    lines = [f"GN ticket summary for {summary_date}.",
             f"All times below are {display_timezone_label()} time.", ""]

    if booked_sessions:
        lines.append(f"BOOKED TODAY ({len(booked_sessions)})")
        lines.append("")
        for session in booked_sessions:
            lines.append(f"- {session.get('title', 'Unknown Session')} | {session.get('school', 'Unknown School')} | {friendly_datetime(session.get('start_time'))}")
            lines.append(f"  Ticket: {session.get('ticket_id', 'Unknown')}")
        lines.append("")
    else:
        lines.append("BOOKED TODAY")
        lines.append("")
        lines.append("- Nothing was booked today.")
        lines.append("")

    if conflict_sessions:
        lines.append(f"CONFLICTS NEEDING YOU ({len(conflict_sessions)})")
        lines.append("")
        for session in conflict_sessions:
            lines.append(f"- {session.get('title', 'Unknown')} | {session.get('school', 'Unknown')} | {friendly_datetime(session.get('start_time'))}")
            if session.get('conflict_details'):
                lines.append(f"  Reason: {session.get('conflict_details')}")
        lines.append("")
        lines.append("These were not booked. Resolve them in Airtable and the next run will pick them up.")
        _append_teacher_emails(lines, conflict_sessions)
    else:
        lines.append("CONFLICTS NEEDING YOU")
        lines.append("")
        lines.append("- None. Everything upcoming is either booked or has no conflict.")

    if excluded_sessions:
        lines.append("")
        lines.append(f"REMOVED, NOT BEING BOOKED ({len(excluded_sessions)})")
        lines.append("")
        for session in excluded_sessions:
            lines.append(f"- {session.get('title', 'Unknown')} | {session.get('school', 'Unknown')} | {friendly_datetime(session.get('start_time'))}")
        lines.append("")
        lines.append("You took these off the list on the dashboard, so no run will book "
                     "them. Put one back with 'Process anyway' if that has changed.")

    subject = f"{subject_prefix}: Daily summary for {summary_date}"
    _send(to_email, subject, "\n".join(lines))
