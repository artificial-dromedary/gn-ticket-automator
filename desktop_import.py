"""Bring someone's setup across from the retired desktop app.

The desktop app kept the same tables this service does, in a SQLite file on the
person's Mac (~/GN_Ticket_Automator/gn_ticket.db), encrypted with a key that was
built into the app rather than the one this service uses. A signed-in person
uploads that file; we read it read-only, unlock their credentials with the desktop
key, and save them here under ours, along with their preferences and history.

Only the row matching the uploader's own sign-in is ever read, so a file with
someone else's profile in it imports nothing.
"""
import os
import sqlite3
from datetime import datetime

from cryptography.fernet import InvalidToken
from sqlalchemy import select

from db import SessionLocal
from models import ConflictEmailLog, TicketSubmission, User
from user_profiles import fernet_for, user_manager

# The key the desktop builds encrypted with. Kept separate from APP_ENCRYPTION_KEY,
# which Render generated for this service and so is not the one on anyone's Mac.
# More than one may be listed, comma-separated: builds handed out at different times
# may carry different keys, and a file is only readable by the one it was written
# with. Each is tried in turn.
DESKTOP_KEY_ENV = "DESKTOP_APP_ENCRYPTION_KEY"

# A desktop database is around 100 KB; anything near this is not one.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

SQLITE_HEADER = b"SQLite format 3\x00"


class DesktopImportError(Exception):
    """Something the person can act on, worded for them."""


def _desktop_fernets():
    keys = os.getenv(DESKTOP_KEY_ENV, "").split(",")
    # Last resort: a build that happened to ship this service's key.
    keys.append(os.getenv("APP_ENCRYPTION_KEY", ""))
    fernets = []
    for key in keys:
        if key.strip():
            try:
                fernets.append(fernet_for(key))
            except RuntimeError:
                continue
    return fernets


def _decrypt(fernets, token):
    if not token:
        return None
    for fernet in fernets:
        try:
            return fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            continue
    raise DesktopImportError(
        "Your saved credentials in that file could not be unlocked. Enter them in "
        "the setup steps instead."
    )


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _parse_time(value):
    if not value or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _open(path):
    with open(path, "rb") as handle:
        if handle.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise DesktopImportError(
                "That file isn't a desktop app database. Choose gn_ticket.db from the "
                "GN_Ticket_Automator folder in your home folder."
            )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_desktop_profile(path, email):
    """Everything worth keeping for `email` from a desktop database, decrypted."""
    email = email.strip().lower()
    conn = _open(path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"users", "user_credentials"} <= tables:
            raise DesktopImportError(
                "That file doesn't have saved settings in it. It may be from a very old "
                "version of the desktop app; enter your details in the setup steps instead."
            )

        user = conn.execute("SELECT id FROM users WHERE lower(email) = ?", (email,)).fetchone()
        creds = user and conn.execute(
            "SELECT airtable_api_key_enc, servicenow_password_enc, totp_secret_enc "
            "FROM user_credentials WHERE user_id = ?", (user["id"],)).fetchone()
        if not creds:
            raise DesktopImportError(
                f"That file has no saved settings for {email}. Make sure you signed in "
                "here with the same account you used in the desktop app."
            )

        fernets = _desktop_fernets()
        profile = {
            "airtable_api_key": _decrypt(fernets, creds["airtable_api_key_enc"]),
            "servicenow_password": _decrypt(fernets, creds["servicenow_password_enc"]),
            "totp_secret": _decrypt(fernets, creds["totp_secret_enc"]),
            "preferences": {},
        }
        if not all(profile[field] for field in ("airtable_api_key", "servicenow_password", "totp_secret")):
            raise DesktopImportError(
                "The desktop app's setup was never finished, so there is nothing complete "
                "to bring across. Enter your details in the setup steps instead."
            )

        if "user_preferences" in tables:
            wanted = ["buffer_before", "buffer_after", "window_past_days",
                      "window_future_days", "scan_frequency_hours"]
            present = [c for c in wanted if c in _columns(conn, "user_preferences")]
            if present:
                row = conn.execute(
                    f"SELECT {', '.join(present)} FROM user_preferences WHERE user_id = ?",
                    (user["id"],)).fetchone()
                if row:
                    profile["preferences"] = {c: row[c] for c in present if row[c] is not None}

        submissions = []
        if "ticket_submissions" in tables:
            submissions = [dict(row) for row in conn.execute(
                "SELECT submitted_at, session_id, title, school, teacher, ticket_id, "
                "start_time, length, status FROM ticket_submissions WHERE user_id = ?",
                (user["id"],))]

        emailed = []
        if "conflict_email_log" in tables:
            emailed = [dict(row) for row in conn.execute(
                "SELECT session_id, conflict_session_id, emailed_at FROM conflict_email_log "
                "WHERE user_id = ?", (user["id"],))]

        return profile, submissions, emailed
    except sqlite3.DatabaseError as exc:
        raise DesktopImportError("That file couldn't be read as a desktop app database.") from exc
    finally:
        conn.close()


def import_desktop_profile(path, email, name=None, replace=False):
    """Save a desktop profile under this service's key. Returns what was brought across.

    Automatic booking is always left off. Someone still running the desktop app
    would otherwise have both booking the same sessions; they turn it on once they
    have stopped using it.
    """
    email = email.strip().lower()
    profile, submissions, emailed = read_desktop_profile(path, email)

    if user_manager.is_profile_complete(email) and not replace:
        raise DesktopImportError(
            "You already have settings saved here. Tick the box to replace them with the "
            "ones from the desktop app."
        )

    user_manager.upsert_user(email, name)
    profile["preferences"]["auto_booking_enabled"] = False
    user_manager.save_profile(email, profile)

    added_submissions = added_emailed = 0
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one()

        known_tickets = set(db.execute(
            select(TicketSubmission.session_id, TicketSubmission.ticket_id)
            .where(TicketSubmission.user_id == user.id)).all())
        for entry in submissions:
            key = (entry["session_id"], entry["ticket_id"])
            if key in known_tickets:
                continue
            known_tickets.add(key)
            db.add(TicketSubmission(
                user_id=user.id,
                submitted_at=_parse_time(entry["submitted_at"]) or datetime.utcnow(),
                session_id=entry["session_id"], title=entry["title"], school=entry["school"],
                teacher=entry["teacher"], ticket_id=entry["ticket_id"],
                start_time=_parse_time(entry["start_time"]), length=entry["length"] or 0,
                status=entry["status"] or "success",
            ))
            added_submissions += 1

        known_emailed = set(db.execute(
            select(ConflictEmailLog.session_id, ConflictEmailLog.conflict_session_id)
            .where(ConflictEmailLog.user_id == user.id)).all())
        for entry in emailed:
            key = (entry["session_id"], entry["conflict_session_id"])
            if not entry["session_id"] or key in known_emailed:
                continue
            known_emailed.add(key)
            db.add(ConflictEmailLog(
                user_id=user.id, session_id=entry["session_id"],
                conflict_session_id=entry["conflict_session_id"],
                emailed_at=_parse_time(entry["emailed_at"]) or datetime.utcnow(),
            ))
            added_emailed += 1

        db.commit()

    return {"submissions": added_submissions, "conflict_emails": added_emailed,
            "preferences": sorted(profile["preferences"])}
