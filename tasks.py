import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import redis
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from airtable_integration import create_airtable_client
from conflict import check_for_time_conflicts
from db import SessionLocal
from emailer import send_booking_summary_email, send_conflict_email
from models import ConflictEmailLog, ScanResult, User
from ticket_submission_log import TicketSubmissionLog
from user_profiles import user_manager
import gn_ticket


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# When there is no Celery broker — the Render Cron Job deployment — tasks run inline in
# the calling process instead of being enqueued. Set GN_INLINE_TASKS=1 there.
INLINE_TASKS = os.getenv("GN_INLINE_TASKS", "").strip().lower() in ("1", "true", "yes")

# A dry run does everything except submit to ServiceNow: it reads Airtable, detects
# conflicts, records the scan, and reports what it *would* have booked.
DRY_RUN = os.getenv("GN_DRY_RUN", "").strip().lower() in ("1", "true", "yes")

# Ceiling on bookings per run, as a blast-radius guard. 0 means no limit.
try:
    MAX_BOOKINGS_PER_RUN = int(os.getenv("GN_MAX_BOOKINGS_PER_RUN", "0"))
except ValueError:
    MAX_BOOKINGS_PER_RUN = 0

# A scan is quick (Airtable reads only); a booking run drives Chrome and can take a while.
SCAN_LOCK_TTL = 10 * 60
BOOK_LOCK_TTL = 2 * 60 * 60

celery_app = Celery("gn_ticket", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.timezone = os.getenv("AUTO_SCAN_TZ", "America/Toronto")
celery_app.conf.beat_schedule = {
    "daily_auto_scan": {
        "task": "tasks.run_scheduled_scan",
        "schedule": crontab(
            hour=int(os.getenv("AUTO_SCAN_HOUR", "6")),
            minute=int(os.getenv("AUTO_SCAN_MINUTE", "0")),
        ),
    }
}

logger = logging.getLogger(__name__)


def auto_scan_time_label():
    """Human-readable description of when the scheduled scan runs."""
    hour = int(os.getenv("AUTO_SCAN_HOUR", "6"))
    minute = int(os.getenv("AUTO_SCAN_MINUTE", "0"))
    return f"{hour:02d}:{minute:02d} {celery_app.conf.timezone}"


@contextmanager
def _user_lock(name, ttl):
    """Best-effort cross-process lock so one user never gets two concurrent runs.

    If Redis is unreachable the work is allowed through — the broker would be down
    too, so this only matters for direct/local invocations.
    """
    key = f"gn:lock:{name}"
    token = uuid.uuid4().hex
    client = None
    acquired = True

    try:
        client = redis.Redis.from_url(REDIS_URL)
        acquired = bool(client.set(key, token, nx=True, ex=ttl))
    except Exception as exc:
        logger.warning("Lock unavailable for %s (%s); proceeding without it.", key, exc)
        client = None

    try:
        yield acquired
    finally:
        if client is not None and acquired:
            try:
                if client.get(key) == token.encode():
                    client.delete(key)
            except Exception as exc:
                logger.warning("Could not release lock %s: %s", key, exc)


def _session_to_dict(session):
    return {
        "session_id": session.s_id,
        "title": session.title,
        "school": session.school,
        "teacher": session.teacher,
        "start_time": session.start_time.isoformat() if session.start_time else None,
        "length": session.length,
        "conflict_details": session.conflict_details,
        "conflict_type": session.conflict_type,
        "conflict_session_id": getattr(session, "conflict_other_id", None),
        "conflict_start_iso": session.conflict_start_iso,
        "conflict_end_iso": session.conflict_end_iso,
    }


def _annotate_conflicts(airtable_client, candidate_sessions, user_email,
                        window_past_days, window_future_days):
    """Flag each candidate that collides with an existing booking or a submitted ticket."""
    if not candidate_sessions:
        return []

    school_names = list({s.school for s in candidate_sessions if s.school != "Unknown School"})
    existing_sessions = airtable_client.get_all_sessions_for_schools(
        school_names,
        status_filters=["Booked"],
        window_past_days=window_past_days,
        window_future_days=window_future_days,
    ) if school_names else []

    historical_entries = TicketSubmissionLog().get_entries(user_email)
    return check_for_time_conflicts(candidate_sessions, existing_sessions, historical_entries)


def _conflict_key(entry):
    return (entry.get("session_id"), entry.get("conflict_session_id") or "")


def _email_new_conflicts(user_email, conflict_payload):
    """Email only conflicts we haven't already reported, then record what we sent."""
    if not conflict_payload:
        return []

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return []
        already_emailed = {
            (row.session_id, row.conflict_session_id or "")
            for row in db.execute(
                select(ConflictEmailLog).where(ConflictEmailLog.user_id == user.id)
            ).scalars().all()
        }

    unreported = [c for c in conflict_payload if _conflict_key(c) not in already_emailed]
    if not unreported:
        logger.info("All %d conflict(s) for %s already reported; skipping email.",
                    len(conflict_payload), user_email)
        return []

    send_conflict_email(user_email, unreported)

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if user:
            for entry in unreported:
                session_id = entry.get("session_id")
                if not session_id:
                    continue
                db.execute(sa_delete(ConflictEmailLog).where(
                    ConflictEmailLog.user_id == user.id,
                    ConflictEmailLog.session_id == session_id,
                ))
                db.add(ConflictEmailLog(
                    user_id=user.id,
                    session_id=session_id,
                    conflict_session_id=entry.get("conflict_session_id"),
                ))
            db.commit()

    return unreported


def _record_scan(user_email, candidate_sessions, conflict_payload, clean_sessions):
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return
        db.add(ScanResult(
            user_id=user.id,
            scanned_at=datetime.now(timezone.utc),
            conflicts_json=json.dumps(conflict_payload),
            candidate_ids=json.dumps([s.s_id for s in candidate_sessions]),
            summary=json.dumps({
                "candidates": len(candidate_sessions),
                "conflicts": len(conflict_payload),
                "clean": len(clean_sessions),
            }),
        ))
        db.commit()


def _record_booking_outcome(user_email, successful, failed):
    """Fold the booking result into the most recent scan so the dashboard can show it."""
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return
        scan = db.execute(
            select(ScanResult)
            .where(ScanResult.user_id == user.id)
            .order_by(ScanResult.scanned_at.desc())
        ).scalars().first()
        if not scan:
            return

        try:
            summary = json.loads(scan.summary) if scan.summary else {}
        except (TypeError, json.JSONDecodeError):
            summary = {}

        summary["booked"] = len(successful)
        summary["failed"] = len(failed)
        summary["booked_at"] = datetime.now(timezone.utc).isoformat()
        scan.summary = json.dumps(summary)
        db.commit()


def dispatch_scan(user_email):
    """Queue a scan, or run it here when there is no broker."""
    if INLINE_TASKS:
        return scan_user(user_email)
    return scan_user.delay(user_email)


def dispatch_booking(user_email, session_ids):
    """Queue a booking run, or run it here when there is no broker."""
    if INLINE_TASKS:
        return book_sessions(user_email, session_ids)
    return book_sessions.delay(user_email, session_ids)


@celery_app.task(name="tasks.run_scheduled_scan")
def run_scheduled_scan():
    emails = user_manager.list_auto_enabled_users()
    logger.info("Scheduled scan starting for %d opted-in user(s).", len(emails))
    for email in emails:
        try:
            dispatch_scan(email)
        except Exception as exc:
            logger.error("Scan failed for %s: %s", email, exc, exc_info=True)


@celery_app.task(name="tasks.run_hourly_scan")
def run_hourly_scan():
    """Deprecated alias kept so tasks queued under the old name still resolve."""
    return run_scheduled_scan()


@celery_app.task(name="tasks.scan_user")
def scan_user(user_email):
    with _user_lock(f"scan:{user_email}", SCAN_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("Scan for %s already running; skipping.", user_email)
            return
        _scan_user(user_email)


def _scan_user(user_email):
    if not user_manager.is_profile_complete(user_email):
        logger.info("Skipping scan for %s: profile incomplete.", user_email)
        return

    profile = user_manager.load_profile(user_email)
    if not profile:
        return

    prefs = profile.get("preferences", {})
    window_past_days = prefs.get("window_past_days", 14)
    window_future_days = prefs.get("window_future_days", 90)

    airtable_client = create_airtable_client(profile["airtable_api_key"])
    candidate_sessions = airtable_client.get_booked_sessions(
        user_email=user_email,
        window_past_days=window_past_days,
        window_future_days=window_future_days,
    )
    candidate_sessions = _annotate_conflicts(
        airtable_client, candidate_sessions, user_email, window_past_days, window_future_days
    )

    conflicts = [s for s in candidate_sessions if s.is_conflict]
    clean = [s for s in candidate_sessions if not s.is_conflict]
    conflict_payload = [_session_to_dict(s) for s in conflicts]

    logger.info("Scan for %s: %d candidate(s), %d clean, %d conflicted.",
                user_email, len(candidate_sessions), len(clean), len(conflicts))

    _record_scan(user_email, candidate_sessions, conflict_payload, clean)

    # Conflicts are reported but never block the sessions that are fine.
    if conflict_payload:
        try:
            _email_new_conflicts(user_email, conflict_payload)
        except Exception as exc:
            logger.error("Could not send conflict email to %s: %s", user_email, exc)

    if clean:
        dispatch_booking(user_email, [s.s_id for s in clean])


@celery_app.task(name="tasks.book_sessions")
def book_sessions(user_email, session_ids):
    with _user_lock(f"book:{user_email}", BOOK_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("Booking for %s already running; skipping this batch.", user_email)
            return
        _book_sessions(user_email, session_ids)


def _book_sessions(user_email, session_ids):
    if not user_manager.is_profile_complete(user_email):
        logger.info("Skipping booking for %s: profile incomplete.", user_email)
        return

    profile = user_manager.load_profile(user_email)
    if not profile:
        return

    prefs = profile.get("preferences", {})
    window_past_days = prefs.get("window_past_days", 14)
    window_future_days = prefs.get("window_future_days", 90)
    buffer_before = prefs.get("buffer_before", 10)
    buffer_after = prefs.get("buffer_after", 10)

    airtable_client = create_airtable_client(profile["airtable_api_key"])
    candidate_sessions = airtable_client.get_booked_sessions(
        user_email=user_email,
        window_past_days=window_past_days,
        window_future_days=window_future_days,
    )
    # Re-check: a conflict may have appeared between the scan and now.
    candidate_sessions = _annotate_conflicts(
        airtable_client, candidate_sessions, user_email, window_past_days, window_future_days
    )

    requested = set(session_ids)
    send_to_gn = [s for s in candidate_sessions if s.s_id in requested and not s.is_conflict]

    skipped = len(requested) - len(send_to_gn)
    if skipped > 0:
        logger.info("Dropping %d of %d requested session(s) for %s: no longer bookable.",
                    skipped, len(requested), user_email)

    if not send_to_gn:
        return

    if MAX_BOOKINGS_PER_RUN and len(send_to_gn) > MAX_BOOKINGS_PER_RUN:
        logger.warning(
            "Capping this run at %d of %d bookable session(s) for %s (GN_MAX_BOOKINGS_PER_RUN). "
            "The remainder are picked up on the next run.",
            MAX_BOOKINGS_PER_RUN, len(send_to_gn), user_email,
        )
        send_to_gn = send_to_gn[:MAX_BOOKINGS_PER_RUN]

    if DRY_RUN:
        logger.warning(
            "DRY RUN for %s: would submit %d ticket(s) and is submitting none. Sessions: %s",
            user_email, len(send_to_gn),
            ", ".join(f"{s.s_id} ({s.title} @ {s.school})" for s in send_to_gn),
        )
        _record_booking_outcome(user_email, [], [])
        return {"dry_run": True, "would_book": [s.s_id for s in send_to_gn]}

    gn_ticket.set_progress_callback(lambda *args, **kwargs: None)

    booking_results = gn_ticket.gn_ticket_handler(
        send_to_gn,
        user_email,
        profile.get("servicenow_password"),
        "connectednorth@takingitglobal.org",
        None,
        profile.get("airtable_api_key"),
        profile.get("totp_secret"),
        headless_mode=True,
        allow_manual_site_selection=False,
        chatgpt_api_key=os.getenv("CHATGPT_API_KEY"),
        buffer_before=buffer_before,
        buffer_after=buffer_after,
    )

    successful = booking_results.get("successful_sessions", [])
    failed = booking_results.get("failed_sessions", [])

    TicketSubmissionLog().add_successful_submissions(user_email, successful)
    _record_booking_outcome(user_email, successful, failed)

    logger.info("Booking for %s: %d succeeded, %d failed.", user_email, len(successful), len(failed))

    try:
        send_booking_summary_email(user_email, successful, failed)
    except Exception as exc:
        logger.error("Could not send booking summary to %s: %s", user_email, exc)
