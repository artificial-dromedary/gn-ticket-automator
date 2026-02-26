import json
import logging
import os
from datetime import datetime, timezone

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

from airtable_integration import create_airtable_client
from conflict import check_for_time_conflicts
from db import SessionLocal
from emailer import send_conflict_email
from models import ScanResult, User
from ticket_submission_log import TicketSubmissionLog
from user_profiles import user_manager
import gn_ticket


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("gn_ticket", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.beat_schedule = {
    "hourly_auto_scan": {
        "task": "tasks.run_hourly_scan",
        "schedule": crontab(minute=0),
    }
}
celery_app.conf.timezone = "UTC"

logger = logging.getLogger(__name__)


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
        "conflict_start_iso": session.conflict_start_iso,
        "conflict_end_iso": session.conflict_end_iso,
    }


@celery_app.task(name="tasks.run_hourly_scan")
def run_hourly_scan():
    users = user_manager.list_auto_enabled_users()
    for user in users:
        try:
            scan_user.delay(user.email)
        except Exception as exc:
            logger.error("Failed to enqueue scan for %s: %s", user.email, exc)


@celery_app.task(name="tasks.scan_user")
def scan_user(user_email):
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

    conflicts = []
    if candidate_sessions:
        school_names = list(set(s.school for s in candidate_sessions if s.school != "Unknown School"))
        existing_sessions = airtable_client.get_all_sessions_for_schools(
            school_names,
            status_filters=["Booked"],
            window_past_days=window_past_days,
            window_future_days=window_future_days,
        ) if school_names else []
        historical_entries = TicketSubmissionLog().get_entries(user_email)
        candidate_sessions = check_for_time_conflicts(candidate_sessions, existing_sessions, historical_entries)
        conflicts = [s for s in candidate_sessions if s.is_conflict]

    conflict_payload = [_session_to_dict(s) for s in conflicts]

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
        if not user:
            return
        scan = ScanResult(
            user_id=user.id,
            scanned_at=datetime.now(timezone.utc),
            conflicts_json=json.dumps(conflict_payload),
            candidate_ids=json.dumps([s.s_id for s in candidate_sessions]),
            summary=json.dumps({
                "candidates": len(candidate_sessions),
                "conflicts": len(conflict_payload),
            }),
        )
        db.add(scan)
        db.commit()

    if conflict_payload:
        send_conflict_email(user_email, conflict_payload)
        return

    if candidate_sessions:
        book_sessions.delay(user_email, [s.s_id for s in candidate_sessions])


@celery_app.task(name="tasks.book_sessions")
def book_sessions(user_email, session_ids):
    profile = user_manager.load_profile(user_email)
    if not profile:
        return

    airtable_client = create_airtable_client(profile["airtable_api_key"])
    prefs = profile.get("preferences", {})
    window_past_days = prefs.get("window_past_days", 14)
    window_future_days = prefs.get("window_future_days", 90)
    buffer_before = prefs.get("buffer_before", 10)
    buffer_after = prefs.get("buffer_after", 10)

    candidate_sessions = airtable_client.get_booked_sessions(
        user_email=user_email,
        window_past_days=window_past_days,
        window_future_days=window_future_days,
    )

    send_to_gn = [s for s in candidate_sessions if s.s_id in set(session_ids)]
    if not send_to_gn:
        return

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

    TicketSubmissionLog().add_successful_submissions(user_email, booking_results.get("successful_sessions", []))
