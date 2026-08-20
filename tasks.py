import json
import logging
import os
import threading
import time
import uuid
from zoneinfo import ZoneInfo
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from airtable_integration import create_airtable_client
from conflict import check_for_time_conflicts
from db import SessionLocal
from emailer import send_booking_summary_email, send_conflict_email, send_daily_summary_email
from models import (ConflictEmailLog, DailySummaryLog, ScanRequest, ScanResult,
                    SessionExclusion, TaskLock, TicketSubmission, User)
from ticket_submission_log import TicketSubmissionLog
from user_profiles import DEFAULT_SCAN_FREQUENCY_HOURS, user_manager
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

# Lock lifetimes are short because a holder refreshes its own claim while it works
# (see _Heartbeat). Short TTL + refresh means a process that dies mid-run frees its
# lock within minutes instead of wedging the next run for hours.
LOCK_TTL = 5 * 60
LOCK_HEARTBEAT_SECONDS = 60

SCAN_LOCK_TTL = LOCK_TTL
BOOK_LOCK_TTL = LOCK_TTL

# Only one browser-driving booking run at a time, across every user and every
# service. Chrome is the memory ceiling on a small instance, so two at once is how
# a run gets killed rather than how two runs finish faster.
BOOKING_SLOT = "booking-slot"

# The end-of-day summary. The job fires hourly and sends it on the first run at or
# after this local hour, once per day.
DAILY_SUMMARY_HOUR = int(os.getenv("DAILY_SUMMARY_HOUR", "17"))
DAILY_SUMMARY_TZ = os.getenv("DAILY_SUMMARY_TZ", "America/New_York")

# Waiting beats failing: these runs are never urgent, so a busy service queues.
BUSY_POLL_SECONDS = int(os.getenv("GN_BUSY_POLL_SECONDS", "30"))
BUSY_MAX_WAIT_SECONDS = int(os.getenv("GN_BUSY_MAX_WAIT_SECONDS", str(60 * 60)))
# How often to announce that we are still waiting, so logs don't fill with notices.
BUSY_NOTICE_SECONDS = int(os.getenv("GN_BUSY_NOTICE_SECONDS", "300"))

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


def auto_scan_time_label(frequency_hours=None):
    """Human-readable description of how often the scheduled scan runs."""
    hours = frequency_hours or DEFAULT_SCAN_FREQUENCY_HOURS
    if hours == 1:
        return "every hour"
    if hours == 24:
        return "once a day"
    return f"every {hours} hours"


def _try_acquire_lock(name, token, ttl):
    """Claim `name` for `token`, or return False if someone else holds it.

    The unique constraint on TaskLock.name is what makes this atomic: two racing
    callers both attempt the insert and the database rejects one of them.
    """
    now = datetime.utcnow()
    with SessionLocal() as db:
        # Reclaim a lock whose holder died before releasing it.
        db.execute(sa_delete(TaskLock).where(TaskLock.name == name, TaskLock.expires_at <= now))
        db.commit()

        db.add(TaskLock(
            name=name,
            token=token,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl),
        ))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


def _release_lock(name, token):
    """Release only our own claim, never one a later run took over."""
    with SessionLocal() as db:
        db.execute(sa_delete(TaskLock).where(TaskLock.name == name, TaskLock.token == token))
        db.commit()


def _renew_lock(name, token, ttl):
    """Push our claim's expiry back. Returns False if we no longer hold it."""
    with SessionLocal() as db:
        result = db.execute(
            sa_update(TaskLock)
            .where(TaskLock.name == name, TaskLock.token == token)
            .values(expires_at=datetime.utcnow() + timedelta(seconds=ttl))
        )
        db.commit()
        return result.rowcount > 0


class _Heartbeat(threading.Thread):
    """Keeps a held lock alive for as long as its holder is actually alive.

    Without this, a lock's TTL has to cover the longest run we can imagine, and a
    crashed holder blocks everyone for that whole window. With it, the TTL only has
    to outlive one heartbeat interval.
    """

    def __init__(self, name, token, ttl):
        super().__init__(daemon=True)
        self.name_ = name
        self.token = token
        self.ttl = ttl
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(LOCK_HEARTBEAT_SECONDS):
            try:
                if not _renew_lock(self.name_, self.token, self.ttl):
                    logger.warning("Lock %s is no longer ours; stopping its heartbeat.", self.name_)
                    return
            except Exception as exc:
                logger.warning("Could not refresh lock %s: %s", self.name_, exc)

    def stop(self):
        self._stop.set()


def _acquire(key, token, ttl, wait_seconds, on_wait):
    """Take `key`, waiting up to `wait_seconds` for whoever holds it to finish."""
    deadline = time.monotonic() + max(wait_seconds, 0)
    next_notice = time.monotonic()

    while True:
        try:
            if _try_acquire_lock(key, token, ttl):
                return True
        except Exception as exc:
            logger.error("Could not reach the lock table for %s (%s); skipping to stay safe.", key, exc)
            return False

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        if on_wait and time.monotonic() >= next_notice:
            on_wait(int(remaining))
            next_notice = time.monotonic() + BUSY_NOTICE_SECONDS

        time.sleep(min(BUSY_POLL_SECONDS, remaining))


@contextmanager
def _user_lock(name, ttl, wait_seconds=0, on_wait=None):
    """Cross-process lock so one user never gets two concurrent runs.

    Backed by the database, which every deployment has — the cron-job architecture
    runs without a broker, so a Redis-backed lock there would grant unconditionally
    and quietly stop being a lock at all. Two overlapping booking runs would each
    read the same Airtable candidates and file duplicate tickets, because a session
    is only marked "GN Ticket Requested" after its ticket is submitted.

    With wait_seconds > 0 a busy lock is waited on rather than skipped, calling
    on_wait(seconds_left) occasionally so the caller can say so. Fails closed: a
    lock that cannot be taken means the work is skipped, never run unprotected.
    """
    key = f"gn:lock:{name}"
    token = uuid.uuid4().hex

    acquired = _acquire(key, token, ttl, wait_seconds, on_wait)
    heartbeat = None
    if acquired:
        heartbeat = _Heartbeat(key, token, ttl)
        heartbeat.start()

    try:
        yield acquired
    finally:
        if heartbeat:
            heartbeat.stop()
        if acquired:
            try:
                _release_lock(key, token)
            except Exception as exc:
                logger.warning("Could not release lock %s: %s; it expires on its own.", key, exc)


@contextmanager
def booking_slot(wait_seconds=None, on_wait=None):
    """The single browser slot. Held only while Chrome is actually running."""
    if wait_seconds is None:
        wait_seconds = BUSY_MAX_WAIT_SECONDS
    with _user_lock(BOOKING_SLOT, LOCK_TTL, wait_seconds=wait_seconds, on_wait=on_wait) as acquired:
        yield acquired


def booking_in_progress():
    """When a booking run is under way, since when. None when the slot is free."""
    with SessionLocal() as db:
        row = db.execute(
            select(TaskLock).where(
                TaskLock.name == f"gn:lock:{BOOKING_SLOT}",
                TaskLock.expires_at > datetime.utcnow(),
            )
        ).scalars().first()
        return row.acquired_at if row else None


def busy_notice(seconds_left):
    """The message a waiting run reports while the slot is taken."""
    minutes = max(int(seconds_left), 0) // 60
    left = f"{minutes} more min" if minutes else "under a minute"
    return ("Another booking run is in progress. Waiting for it to finish, then this one "
            f"starts automatically — giving up after {left}.")


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


def dispatch_booking(user_email, session_ids, manual=False):
    """Queue a booking run, or run it here when there is no broker."""
    if INLINE_TASKS:
        return book_sessions(user_email, session_ids, manual)
    return book_sessions.delay(user_email, session_ids, manual)


def last_scan_at(user_email):
    """When this user was last scanned, or None if never."""
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return None
        return db.execute(
            select(ScanResult.scanned_at)
            .where(ScanResult.user_id == user.id)
            .order_by(ScanResult.scanned_at.desc())
        ).scalars().first()


def request_scan(user_email):
    """Record that this user wants a scan before their interval is up."""
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return False
        pending = db.execute(
            select(ScanRequest).where(ScanRequest.user_id == user.id)
        ).scalars().first()
        if not pending:
            db.add(ScanRequest(user_id=user.id))
            db.commit()
        return True


def has_pending_request(user_email):
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return False
        return db.execute(
            select(ScanRequest).where(ScanRequest.user_id == user.id)
        ).scalars().first() is not None


def clear_scan_request(user_email):
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return
        db.execute(sa_delete(ScanRequest).where(ScanRequest.user_id == user.id))
        db.commit()


def excluded_session_ids(user_email):
    """Sessions this person removed on the dashboard. Never booked automatically."""
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return set()
        return {
            session_id for (session_id,) in db.execute(
                select(SessionExclusion.session_id).where(SessionExclusion.user_id == user.id)
            ).all()
        }


def set_session_excluded(user_email, session_id, excluded, title=None, school=None,
                         start_time=None):
    """Record, or undo, a decision not to book a session. Idempotent either way."""
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return False

        if not excluded:
            db.execute(sa_delete(SessionExclusion).where(
                SessionExclusion.user_id == user.id,
                SessionExclusion.session_id == session_id,
            ))
            db.commit()
            return True

        existing = db.execute(select(SessionExclusion).where(
            SessionExclusion.user_id == user.id,
            SessionExclusion.session_id == session_id,
        )).scalars().first()
        if existing:
            # Already removed; refresh the snapshot in case the session moved.
            existing.title = title or existing.title
            existing.school = school or existing.school
            existing.start_time = start_time or existing.start_time
        else:
            db.add(SessionExclusion(user_id=user.id, session_id=session_id, title=title,
                                    school=school, start_time=start_time))
        db.commit()
        return True


def outstanding_exclusions(user_email, now=None):
    """Removed sessions that have not happened yet — the ones still being held back.

    A session in the past can never be booked again, so listing it would just grow
    the daily email forever.
    """
    now = now or datetime.utcnow()
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return []
        rows = db.execute(
            select(SessionExclusion)
            .where(SessionExclusion.user_id == user.id)
            .order_by(SessionExclusion.start_time.asc())
        ).scalars().all()

    upcoming = []
    for row in rows:
        if row.start_time and row.start_time.replace(tzinfo=None) < now:
            continue
        upcoming.append({
            "session_id": row.session_id,
            "title": row.title,
            "school": row.school,
            "start_time": row.start_time.replace(tzinfo=timezone.utc).isoformat()
            if row.start_time else None,
        })
    return upcoming


def user_is_due(user_email, now=None):
    """Whether this user's chosen interval has elapsed since their last scan.

    The cron job fires hourly and asks this of everyone, so a user on a 24 hour
    interval is simply skipped 23 times out of 24 — unless they have asked for a
    scan explicitly, which overrides the interval.
    """
    if has_pending_request(user_email):
        return True

    prefs = user_manager.get_preferences(user_email) or {}
    hours = prefs.get("scan_frequency_hours") or DEFAULT_SCAN_FREQUENCY_HOURS

    previous = last_scan_at(user_email)
    if previous is None:
        return True

    now = now or datetime.utcnow()
    if previous.tzinfo is not None:
        previous = previous.replace(tzinfo=None)

    # A little grace, or an hourly job whose runs drift by seconds would push a
    # 1 hour interval out to 2 hours.
    return previous + timedelta(hours=hours) - timedelta(minutes=5) <= now


@celery_app.task(name="tasks.run_scheduled_scan")
def run_scheduled_scan(force=False):
    emails = user_manager.list_auto_enabled_users()
    due = emails if force else [email for email in emails if user_is_due(email)]

    logger.info("Scheduled scan: %d opted-in user(s), %d due now.", len(emails), len(due))
    for email in due:
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

    # Sessions the person took off the list stay off it. Held back before anything
    # else looks at them, so a removed session is never booked and never emailed
    # about as a conflict either — the decision has already been made.
    excluded = excluded_session_ids(user_email)
    conflicts = [s for s in candidate_sessions if s.is_conflict and s.s_id not in excluded]
    clean = [s for s in candidate_sessions
             if not s.is_conflict and s.s_id not in excluded]
    conflict_payload = [_session_to_dict(s) for s in conflicts]

    logger.info("Scan for %s: %d candidate(s), %d clean, %d conflicted.",
                user_email, len(candidate_sessions), len(clean), len(conflicts))

    _record_scan(user_email, candidate_sessions, conflict_payload, clean)
    # A pending request means a person pressed "Book now" and is waiting on an email.
    # Read it before clearing, since clearing is what marks the request as served.
    manual = has_pending_request(user_email)
    # Satisfied: this scan covers whatever prompted the request.
    clear_scan_request(user_email)

    # Conflicts are reported but never block the sessions that are fine.
    if conflict_payload:
        try:
            _email_new_conflicts(user_email, conflict_payload)
        except Exception as exc:
            logger.error("Could not send conflict email to %s: %s", user_email, exc)

    if clean:
        dispatch_booking(user_email, [s.s_id for s in clean], manual=manual)
    elif manual:
        # Nothing to book, but the button promised an email either way.
        try:
            send_booking_summary_email(user_email, [], [],
                                       conflict_sessions=conflict_payload, manual=True)
        except Exception as exc:
            logger.error("Could not send booking summary to %s: %s", user_email, exc)


@celery_app.task(name="tasks.book_sessions")
def book_sessions(user_email, session_ids, manual=False):
    with _user_lock(f"book:{user_email}", BOOK_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("Booking for %s already running; skipping this batch.", user_email)
            return
        _book_sessions(user_email, session_ids, manual=manual)


def _book_sessions(user_email, session_ids, manual=False):
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

    # Re-read rather than trusting the ids we were handed: someone may have removed a
    # session on the dashboard between the scan and this run.
    excluded = excluded_session_ids(user_email)
    requested = {sid for sid in session_ids if sid not in excluded}
    send_to_gn = [s for s in candidate_sessions if s.s_id in requested and not s.is_conflict]
    conflicted = [_session_to_dict(s) for s in candidate_sessions
                  if s.is_conflict and s.s_id not in excluded]

    def report(successful=None, failed=None):
        """Tell the person who pressed the button how it went. Only they are waiting
        on it — a scheduled run reports through the daily summary instead."""
        if not manual:
            return
        try:
            send_booking_summary_email(user_email, successful or [], failed or [],
                                       conflict_sessions=conflicted, manual=True)
        except Exception as exc:
            logger.error("Could not send booking summary to %s: %s", user_email, exc)

    removed = len(session_ids) - len(requested)
    if removed > 0:
        logger.info("Holding back %d of %d session(s) for %s: removed on the dashboard.",
                    removed, len(session_ids), user_email)

    skipped = len(requested) - len(send_to_gn)
    if skipped > 0:
        logger.info("Dropping %d of %d requested session(s) for %s: no longer bookable.",
                    skipped, len(requested), user_email)

    if not send_to_gn:
        report()
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
        report()
        return {"dry_run": True, "would_book": [s.s_id for s in send_to_gn]}

    gn_ticket.set_progress_callback(lambda *args, **kwargs: None)

    # Take the browser slot only now: the Airtable reads and conflict re-check above
    # do not need it, and holding it for them would make everyone else wait longer.
    def announce(seconds_left):
        logger.info("%s (%s)", busy_notice(seconds_left), user_email)

    with booking_slot(on_wait=announce) as slot:
        if not slot:
            logger.warning(
                "Browser slot still busy after %d min for %s. Leaving these %d session(s) "
                "for the next run — nothing is lost, they stay unbooked in Airtable.",
                BUSY_MAX_WAIT_SECONDS // 60, user_email, len(send_to_gn),
            )
            report(failed=[{"title": f"{len(send_to_gn)} session(s)",
                            "error": "The browser was still busy after a long wait. Nothing "
                                     "was booked; the next run picks these up."}])
            return

        try:
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
        except Exception as exc:
            # Someone is waiting on an email for this run; a silent crash looks
            # identical to a run that is still going.
            logger.error("Booking run failed for %s: %s", user_email, exc, exc_info=True)
            report(failed=[{"title": f"{len(send_to_gn)} session(s)", "error": str(exc)}])
            raise

    successful = booking_results.get("successful_sessions", [])
    failed = booking_results.get("failed_sessions", [])

    TicketSubmissionLog().add_successful_submissions(user_email, successful)
    _record_booking_outcome(user_email, successful, failed)

    logger.info("Booking for %s: %d succeeded, %d failed.", user_email, len(successful), len(failed))
    report(successful=successful, failed=failed)


def _local_now():
    return datetime.now(ZoneInfo(DAILY_SUMMARY_TZ))


def _local_day_bounds_utc(local_date):
    """The UTC instants bracketing a local calendar day.

    Submissions are stored as naive UTC, but "today" means today where the person
    is, so the window has to be built in their zone and converted back.
    """
    zone = ZoneInfo(DAILY_SUMMARY_TZ)
    start_local = datetime.combine(local_date, datetime.min.time(), tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return (start_local.astimezone(timezone.utc).replace(tzinfo=None),
            end_local.astimezone(timezone.utc).replace(tzinfo=None))


def sessions_booked_on(user_email, local_date):
    start_utc, end_utc = _local_day_bounds_utc(local_date)
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return []
        rows = db.execute(
            select(TicketSubmission)
            .where(TicketSubmission.user_id == user.id,
                   TicketSubmission.submitted_at >= start_utc,
                   TicketSubmission.submitted_at < end_utc)
            .order_by(TicketSubmission.submitted_at)
        ).scalars().all()

    return [{
        "title": row.title,
        "school": row.school,
        "teacher": row.teacher,
        "ticket_id": row.ticket_id,
        "start_time": row.start_time.isoformat() if row.start_time else None,
    } for row in rows]


def outstanding_conflicts(user_email):
    """Conflicts from the most recent scan — what still needs a person."""
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return []
        scan = db.execute(
            select(ScanResult)
            .where(ScanResult.user_id == user.id)
            .order_by(ScanResult.scanned_at.desc())
        ).scalars().first()

    if not scan or not scan.conflicts_json:
        return []
    try:
        return json.loads(scan.conflicts_json)
    except (TypeError, json.JSONDecodeError):
        return []


def _summary_already_sent(user_email, summary_date):
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return True
        return db.execute(
            select(DailySummaryLog).where(DailySummaryLog.user_id == user.id,
                                          DailySummaryLog.summary_date == summary_date)
        ).scalars().first() is not None


def _record_summary_sent(user_email, summary_date):
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if user:
            db.add(DailySummaryLog(user_id=user.id, summary_date=summary_date))
            db.commit()


def send_daily_summaries(force=False):
    """Send each opted-in user their end-of-day summary, once per day.

    Called on every scheduled run. Most of them are before the cut-off or have
    already sent today's, and do nothing.
    """
    now_local = _local_now()
    if not force and now_local.hour < DAILY_SUMMARY_HOUR:
        return 0

    summary_date = now_local.date().isoformat()
    sent = 0

    for email in user_manager.list_auto_enabled_users():
        if not force and _summary_already_sent(email, summary_date):
            continue
        try:
            send_daily_summary_email(
                email,
                sessions_booked_on(email, now_local.date()),
                outstanding_conflicts(email),
                summary_date,
                excluded_sessions=outstanding_exclusions(email),
            )
            _record_summary_sent(email, summary_date)
            sent += 1
        except Exception as exc:
            logger.error("Could not send the daily summary to %s: %s", email, exc)

    if sent:
        logger.info("Sent %d daily summary email(s) for %s.", sent, summary_date)
    return sent
