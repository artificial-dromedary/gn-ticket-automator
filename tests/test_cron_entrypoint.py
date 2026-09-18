"""Tests for the cron job entrypoint and the two safety guards it relies on:
dry run and the per-run booking cap.
"""
import pytest

import tasks
import run_scan
from user_profiles import user_manager

from test_tasks import FakeAirtable, USER_EMAIL, registered_user, wired  # noqa: F401


@pytest.fixture
def inline(monkeypatch):
    """Book for real, in this process: the `wired` fixture stubs the scan-to-booking
    hand-off so scan tests can inspect it, and these tests need it live."""
    monkeypatch.setattr(tasks, "dispatch_booking",
                        lambda email, ids, manual=False: tasks.book_sessions(email, ids, manual))


def test_dispatch_scan_runs_here(monkeypatch, registered_user):
    ran = []
    monkeypatch.setattr(tasks, "_scan_user", lambda email: ran.append(email))

    tasks.dispatch_scan(USER_EMAIL)

    assert ran == [USER_EMAIL]


def test_a_scan_books_its_clean_sessions_in_process(monkeypatch, wired, inline):
    booked = []

    def fake_handler(send_to_gn, *args, **kwargs):
        booked.extend(s.s_id for s in send_to_gn)
        return {"successful_sessions": [], "failed_sessions": []}

    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", fake_handler)

    tasks.scan_user(USER_EMAIL)

    assert booked == ["recClean1", "recClean2"]


def test_dry_run_submits_nothing(monkeypatch, wired, inline):
    monkeypatch.setattr(tasks, "DRY_RUN", True)
    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler",
                        lambda *a, **k: pytest.fail("dry run must not submit tickets"))

    tasks.scan_user(USER_EMAIL)

    # The scan itself still happened: conflicts were still detected and reported.
    assert len(wired["conflict_emails"]) == 1


def test_booking_cap_limits_one_run_and_leaves_the_rest(monkeypatch, wired, inline):
    monkeypatch.setattr(tasks, "MAX_BOOKINGS_PER_RUN", 1)
    booked = []

    def fake_handler(send_to_gn, *args, **kwargs):
        booked.extend(s.s_id for s in send_to_gn)
        return {"successful_sessions": [], "failed_sessions": []}

    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", fake_handler)

    tasks.scan_user(USER_EMAIL)

    assert booked == ["recClean1"]


def test_no_cap_by_default(monkeypatch, wired, inline):
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
