import os
import smtplib
from email.message import EmailMessage


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
        lines.append(f"- {session.get('title', 'Unknown')} | {session.get('school', 'Unknown')} | {session.get('start_time', 'Unknown')}")
        if session.get('conflict_details'):
            lines.append(f"  Reason: {session.get('conflict_details')}")
        if session.get('conflict_start_iso') and session.get('conflict_end_iso'):
            lines.append(f"  Conflict window: {session.get('conflict_start_iso')} – {session.get('conflict_end_iso')}")
        lines.append("")

    lines.append("Resolve these in Airtable, or book them by hand from the dashboard.")

    _send(to_email, f"{subject_prefix}: Conflicts Found", "\n".join(lines))


def send_booking_summary_email(to_email, successful_sessions, failed_sessions,
                               subject_prefix="GN Ticket Auto-Booking"):
    """Report what an automated booking run submitted, and what it could not."""
    successful_sessions = successful_sessions or []
    failed_sessions = failed_sessions or []

    if not successful_sessions and not failed_sessions:
        return

    lines = []

    if successful_sessions:
        lines.append(f"Booked {len(successful_sessions)} session(s) with GN:")
        lines.append("")
        for session in successful_sessions:
            lines.append(f"- {session.get('title', 'Unknown Session')} | {session.get('school', 'Unknown School')} | {session.get('start_time', 'Unknown')}")
            lines.append(f"  Ticket: {session.get('ticket_id', 'Unknown')}")
        lines.append("")

    if failed_sessions:
        lines.append(f"{len(failed_sessions)} session(s) could not be booked:")
        lines.append("")
        for session in failed_sessions:
            lines.append(f"- {session.get('title', 'Unknown Session')}")
            lines.append(f"  Error: {session.get('error', 'Unknown error')}")
        lines.append("")
        lines.append("These will be retried on the next scheduled run.")

    subject = f"{subject_prefix}: Booked {len(successful_sessions)} session(s)"
    if failed_sessions:
        subject += f", {len(failed_sessions)} failed"

    _send(to_email, subject, "\n".join(lines))
