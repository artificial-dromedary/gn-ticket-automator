"""Tests for the broker-free (Render Cron Job) execution path.

The Celery task definitions are unchanged; what these cover is the inline dispatch
that lets the same code run in a single short-lived process, plus the two safety
guards the cron deployment relies on: dry run and the per-run booking cap.
"""
import pytest

import tasks
import run_scan
from user_profiles import user_manager

from test_tasks import FakeAirtable, USER_EMAIL, registered_user, wired  # noqa: F401


@pytest.fixture
def inline(monkeypatch):
    monkeypatch.setattr(tasks, "INLINE_TASKS", True)


def test_dispatch_scan_enqueues_when_a_broker_is_configured(monkeypatch, registered_user):
    seen = []
    monkeypatch.setattr(tasks, "INLINE_TASKS", False)
    monkeypatch.setattr(tasks.scan_user, "delay", lambda email: seen.append(email))

    tasks.dispatch_scan(USER_EMAIL)

    assert seen == [USER_EMAIL]


def test_dispatch_scan_runs_here_when_there_is_no_broker(monkeypatch, inline, registered_user):
    ran = []
    monkeypatch.setattr(tasks, "_scan_user", lambda email: ran.append(email))
    monkeypatch.setattr(tasks.scan_user, "delay",
                        lambda email: pytest.fail("should not enqueue in inline mode"))

    tasks.dispatch_scan(USER_EMAIL)

    assert ran == [USER_EMAIL]


def test_inline_scan_books_without_touching_the_queue(monkeypatch, inline, wired):
    """End to end with no broker: a scan should book its clean sessions in-process."""
    booked = []

    def fake_handler(send_to_gn, *args, **kwargs):
        booked.extend(s.s_id for s in send_to_gn)
        return {"successful_sessions": [], "failed_sessions": []}

    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", fake_handler)
    monkeypatch.setattr(tasks.book_sessions, "delay",
                        lambda *a, **k: pytest.fail("should not enqueue in inline mode"))

    tasks.scan_user(USER_EMAIL)

    assert booked == ["recClean1", "recClean2"]


def test_dry_run_submits_nothing(monkeypatch, inline, wired):
    monkeypatch.setattr(tasks, "DRY_RUN", True)
    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler",
                        lambda *a, **k: pytest.fail("dry run must not submit tickets"))

    tasks.scan_user(USER_EMAIL)

    # The scan itself still happened: conflicts were still detected and reported.
    assert len(wired["conflict_emails"]) == 1


def test_booking_cap_limits_one_run_and_leaves_the_rest(monkeypatch, inline, wired):
    monkeypatch.setattr(tasks, "MAX_BOOKINGS_PER_RUN", 1)
    booked = []

    def fake_handler(send_to_gn, *args, **kwargs):
        booked.extend(s.s_id for s in send_to_gn)
        return {"successful_sessions": [], "failed_sessions": []}

    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", fake_handler)

    tasks.scan_user(USER_EMAIL)

    assert booked == ["recClean1"]


def test_no_cap_by_default(monkeypatch, inline, wired):
    booked = []

    def fake_handler(send_to_gn, *args, **kwargs):
        booked.extend(s.s_id for s in send_to_gn)
        return {"successful_sessions": [], "failed_sessions": []}

    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", fake_handler)

    tasks.scan_user(USER_EMAIL)

    assert booked == ["recClean1", "recClean2"]


def test_entrypoint_refuses_to_start_without_an_encryption_key(monkeypatch):
    monkeypatch.delenv("APP_ENCRYPTION_KEY", raising=False)
    assert run_scan.main([]) == 2


def test_entrypoint_lists_opted_in_users(capsys, registered_user):
    assert run_scan.main(["--list-users"]) == 0
    assert USER_EMAIL in capsys.readouterr().out


def test_entrypoint_scans_only_opted_in_users(monkeypatch, registered_user):
    scanned = []
    monkeypatch.setattr(tasks, "dispatch_scan", lambda email: scanned.append(email))

    user_manager.upsert_user("opted.out@takingitglobal.org")
    user_manager.save_profile("opted.out@takingitglobal.org", {
        "airtable_api_key": "k", "servicenow_password": "p", "totp_secret": "s",
        "preferences": {"auto_booking_enabled": False},
    })

    assert run_scan.main([]) == 0
    assert scanned == [USER_EMAIL]


def test_entrypoint_reports_failure_without_crashing(monkeypatch, registered_user):
    def boom(email):
        raise RuntimeError("airtable down")

    monkeypatch.setattr(tasks, "dispatch_scan", boom)

    assert run_scan.main([]) == 1
