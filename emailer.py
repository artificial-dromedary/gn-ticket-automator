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


# AP-style month abbreviations: "Sept 24", not strftime's "Sep 24".
_SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "June",
                 "July", "Aug", "Sept", "Oct", "Nov", "Dec"]


def short_date(value, tz=None, fallback="an upcoming"):
    """'Sept 24' in the school's own zone — the date as a teacher would write it."""
    if not value:
        return fallback

    moment = value
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    if not isinstance(moment, datetime):
        return fallback
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    try:
        local = moment.astimezone(ZoneInfo(tz or DISPLAY_TZ))
    except (ZoneInfoNotFoundError, ValueError):
        local = moment.astimezone(ZoneInfo(DISPLAY_TZ))

    return f"{_SHORT_MONTHS[local.month - 1]} {local.day}"


def _field(session, name, default=None):
    """Read a field from a scan payload dict or a live session object alike."""
    if isinstance(session, dict):
        return session.get(name, default)
    return getattr(session, name, default)


def _people(session, plural_name, singular_name):
    """Every name (or address) on a session, however Airtable handed it over.

    A session can be booked by more than one teacher, so the plural field is the
    real answer; the singular one is kept as a fallback for older scan payloads
    that only carried the first.
    """
    values = _field(session, plural_name) or []
    if isinstance(values, str):
        values = [values]
    values = [str(value).strip() for value in values if str(value).strip()]
    if values:
        return values
    single = _field(session, singular_name)
    single = str(single).strip() if single else ""
    return [single] if single else []


def _join_names(names, fallback=""):
    """List people the way a person writes them: 'A', 'A and B', 'A, B and C'."""
    if not names:
        return fallback
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


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

    this_names = _people(session, "teachers", "teacher")
    other_names = _people(session, "conflict_other_teachers", "conflict_other_teacher")

    this = {
        "title": _field(session, "title") or "Session",
        "names": this_names,
        "teacher": _join_names(this_names, "the teacher"),
        "email": ", ".join(_people(session, "teacher_emails", "teacher_email")),
        "start": start,
        "created": _field(session, "created_at") or "",
    }
    other = {
        "title": _field(session, "conflict_other_title") or "Session",
        "names": other_names,
        "teacher": _join_names(other_names, "the other teacher"),
        "email": ", ".join(_people(session, "conflict_other_teacher_emails",
                                   "conflict_other_teacher_email")),
        "start": other_start,
        "created": _field(session, "conflict_other_created_at") or "",
    }

    other_first = (_field(session, "conflict_other_ticketed")
                   or not (this["created"] and other["created"])
                   or other["created"] <= this["created"])
    previous, current = (other, this) if other_first else (this, other)
    by_time = sorted([this, other], key=lambda s: s["start"] or "")
    return by_time, previous, current


def _moving_side(session):
    """The session being asked onto Zoom: the one that does not keep the machine."""
    pair = _conflict_pair(session)
    if not pair:
        return None
    _, _, current = pair
    return current


def teacher_conflict_recipients(session):
    """Who the conflict email goes to, comma separated, or "".

    Only the teacher whose session would move to Zoom. The other class keeps the
    Cisco machine and nothing about it changes, so there is nothing to ask them.
    """
    moving = _moving_side(session)
    return moving["email"] if moving else ""


def teacher_conflict_subject(session):
    """'FYI - Connect via Zoom for Sept 24 Connected North session', or None."""
    moving = _moving_side(session)
    if not moving:
        return None
    when = short_date(moving["start"], _field(session, "timezone") or None)
    return f"FYI - Connect via Zoom for {when} Connected North session"


def teacher_conflict_email(session):
    """The copy/paste email to the teacher whose class would join by Zoom, or None.

    Deliberately says nothing about the other class or its teacher: this goes to one
    person about their own session, and the overlap is only the reason for it.
    """
    if not _moving_side(session):
        return None
    return (
        "Hi there,\n\n"
        "I just wanted to let you know that there are two sessions overlapping for "
        "your school. In most cases, as long as your internet is pretty reliable, you "
        "can connect from the classroom via Zoom (instead of the Cisco videoconference "
        "machine). If you've had trouble with Zoom and video streaming in the past, "
        "let me know and we can rebook your session."
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
            drafts.append((session, _moving_side(session), draft))

    if not drafts:
        return

    divider = "-" * 60
    lines.append("")
    lines.append(f"DRAFT EMAIL{'S' if len(drafts) > 1 else ''} TO TEACHERS ({len(drafts)})")
    for session, moving, draft in drafts:
        lines.append("")
        lines.append(f"School: {session.get('school', 'Unknown')}")
        # Only the teacher moving to Zoom: the other class keeps the machine and is
        # not being asked for anything.
        lines.append(f"  To: {moving['teacher']} <{moving['email']}>")
        lines.append(f"  Subject: {teacher_conflict_subject(session)}")
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
