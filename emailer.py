import os
import smtplib
from email.message import EmailMessage


def send_conflict_email(to_email, conflict_sessions, subject_prefix="GN Ticket Auto-Booking"):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)

    if not smtp_user or not smtp_pass or not smtp_from:
        raise RuntimeError("SMTP_USER/SMTP_PASS/SMTP_FROM must be configured for email alerts.")

    subject = f"{subject_prefix}: Conflicts Found"

    lines = [
        "The hourly GN Ticket scan found conflicts:",
        "",
    ]

    for session in conflict_sessions:
        lines.append(f"- {session.get('title', 'Unknown')} | {session.get('school', 'Unknown')} | {session.get('start_time', 'Unknown')}")
        if session.get('conflict_details'):
            lines.append(f"  Reason: {session.get('conflict_details')}")
        if session.get('conflict_start_iso') and session.get('conflict_end_iso'):
            lines.append(f"  Conflict window: {session.get('conflict_start_iso')} – {session.get('conflict_end_iso')}")
        lines.append("")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.set_content("\n".join(lines))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
