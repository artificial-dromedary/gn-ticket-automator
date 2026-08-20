import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Timestamps are stored as naive UTC but read by people in one place, so anything
# shown on a page or in an email is rendered in this zone.
DISPLAY_TZ = os.getenv("AUTO_SCAN_TZ", "America/Toronto")


def friendly_datetime(value, fallback="unknown"):
    """Render a timestamp as 'Wed, Aug 19 at 8:36 pm' in the display timezone.

    Accepts an ISO string or a datetime. A naive value is taken as UTC, which is how
    every timestamp in this app is stored.
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
        local = moment.astimezone(ZoneInfo(DISPLAY_TZ))
    except ZoneInfoNotFoundError:
        local = moment.astimezone(timezone.utc)

    # %-d / %-I are not portable to Windows, so strip the padding by hand.
    day = str(local.day)
    hour = str((local.hour % 12) or 12)
    meridiem = "am" if local.hour < 12 else "pm"
    return f"{local:%a}, {local:%b} {day} at {hour}:{local:%M} {meridiem}"


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

    lines = [f"GN ticket summary for {summary_date}.", ""]

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
