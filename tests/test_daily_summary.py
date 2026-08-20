"""The end-of-day summary.

Sent once a day at a local hour rather than after every booking run — with an
hourly job and a short interval, per-run mail would arrive all day. The immediate
conflict email is unaffected and still goes out on any run that finds one.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import tasks
from ticket_submission_log import TicketSubmissionLog

from test_tasks import USER_EMAIL, registered_user  # noqa: F401

EASTERN = ZoneInfo("America/New_York")


@pytest.fixture
def sent(monkeypatch):
    """Capture the summary instead of mailing it."""
    captured = []
    monkeypatch.setattr(tasks, "send_daily_summary_email",
                        lambda email, booked, conflicts, date: captured.append(
                            {"email": email, "booked": booked, "conflicts": conflicts, "date": date}))
    return captured


def at_local(hour, minute=0):
    """A helper to pin 'now' to a given local time today."""
    return datetime.now(EASTERN).replace(hour=hour, minute=minute, second=0, microsecond=0)


def book_a_session(email, ticket_id, when=None):
    TicketSubmissionLog().add_successful_submissions(email, [{
        "session_id": f"rec{ticket_id}", "title": f"Session {ticket_id}",
        "school": "Nakasuk School", "teacher": "T", "ticket_id": ticket_id,
        "start_time": datetime.now(timezone.utc).isoformat(), "length": 60,
    }])
    if when is not None:
        from sqlalchemy import update

        from db import SessionLocal
        from models import TicketSubmission
        with SessionLocal() as db:
            db.execute(update(TicketSubmission)
                       .where(TicketSubmission.ticket_id == ticket_id)
                       .values(submitted_at=when))
            db.commit()


def test_nothing_is_sent_before_the_cut_off(monkeypatch, sent, registered_user):
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(16, 59))

    assert tasks.send_daily_summaries() == 0
    assert sent == []


def test_it_sends_at_the_cut_off(monkeypatch, sent, registered_user):
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(17, 0))

    assert tasks.send_daily_summaries() == 1
    assert sent[0]["email"] == USER_EMAIL


def test_it_sends_only_once_a_day(monkeypatch, sent, registered_user):
    """The job fires hourly; without this the summary would arrive every hour to midnight."""
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(17, 0))
    tasks.send_daily_summaries()

    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(18, 0))
    assert tasks.send_daily_summaries() == 0

    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(23, 0))
    assert tasks.send_daily_summaries() == 0

    assert len(sent) == 1


def test_it_sends_again_the_next_day(monkeypatch, sent, registered_user):
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(17, 0))
    tasks.send_daily_summaries()

    tomorrow = at_local(17, 0) + timedelta(days=1)
    monkeypatch.setattr(tasks, "_local_now", lambda: tomorrow)

    assert tasks.send_daily_summaries() == 1
    assert sent[0]["date"] != sent[1]["date"]


def test_it_lists_what_was_booked_today(monkeypatch, sent, registered_user):
    book_a_session(USER_EMAIL, "REQ001")
    book_a_session(USER_EMAIL, "REQ002")
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(17, 30))

    tasks.send_daily_summaries()

    assert sorted(s["ticket_id"] for s in sent[0]["booked"]) == ["REQ001", "REQ002"]


def test_yesterdays_bookings_are_not_in_todays_summary(monkeypatch, sent, registered_user):
    book_a_session(USER_EMAIL, "TODAY")
    book_a_session(USER_EMAIL, "YESTERDAY",
                   when=datetime.utcnow() - timedelta(days=1, hours=2))
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(17, 30))

    tasks.send_daily_summaries()

    assert [s["ticket_id"] for s in sent[0]["booked"]] == ["TODAY"]


def test_it_lists_outstanding_conflicts(monkeypatch, sent, registered_user):
    import json

    from sqlalchemy import select

    from db import SessionLocal
    from models import ScanResult, User

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == USER_EMAIL)).scalar_one()
        db.add(ScanResult(user_id=user.id, scanned_at=datetime.utcnow(), summary="{}",
                          conflicts_json=json.dumps([
                              {"session_id": "recX", "title": "Clashing Session",
                               "school": "Qiqirtaq School", "conflict_details": "Overlaps another booking."}])))
        db.commit()

    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(17, 30))
    tasks.send_daily_summaries()

    assert [c["title"] for c in sent[0]["conflicts"]] == ["Clashing Session"]


def test_a_quiet_day_still_gets_a_summary(monkeypatch, sent, registered_user):
    """Silence should mean 'nothing happened', not 'the job died'."""
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(17, 30))

    tasks.send_daily_summaries()

    assert sent[0]["booked"] == []
    assert sent[0]["conflicts"] == []


def test_force_ignores_the_hour_and_the_once_a_day_rule(monkeypatch, sent, registered_user):
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(9, 0))

    assert tasks.send_daily_summaries(force=True) == 1
    assert tasks.send_daily_summaries(force=True) == 1
    assert len(sent) == 2


def test_one_users_failure_does_not_stop_the_others(monkeypatch, registered_user):
    from user_profiles import user_manager

    user_manager.upsert_user("second@takingitglobal.org")
    user_manager.save_profile("second@takingitglobal.org", {
        "airtable_api_key": "patX", "servicenow_password": "p", "totp_secret": "s",
        "preferences": {"auto_booking_enabled": True},
    })

    reached = []

    def flaky(email, booked, conflicts, date):
        if email == USER_EMAIL:
            raise RuntimeError("SMTP down")
        reached.append(email)

    monkeypatch.setattr(tasks, "send_daily_summary_email", flaky)
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(17, 30))

    tasks.send_daily_summaries()

    assert reached == ["second@takingitglobal.org"]


def test_a_failed_send_is_retried_on_the_next_run(monkeypatch, registered_user):
    """A failure must not be recorded as sent, or the day is silently skipped."""
    monkeypatch.setattr(tasks, "_local_now", lambda: at_local(17, 30))
    monkeypatch.setattr(tasks, "send_daily_summary_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SMTP down")))
    tasks.send_daily_summaries()

    delivered = []
    monkeypatch.setattr(tasks, "send_daily_summary_email",
                        lambda email, *a, **k: delivered.append(email))

    assert tasks.send_daily_summaries() == 1
    assert delivered == [USER_EMAIL]
