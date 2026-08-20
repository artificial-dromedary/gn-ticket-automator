"""'Run scan now' on a service that cannot run a browser.

The web process cannot do the booking, so the button asks Render to start the
scan job, which can. Two things have to hold regardless of whether that call
succeeds: the request is never lost, and a booking already in flight is never
cancelled out from under itself.
"""
import pytest

import main
import render_api
import tasks
from user_profiles import user_manager

from test_tasks import USER_EMAIL, registered_user  # noqa: F401


@pytest.fixture
def client(registered_user):
    main.app.config["TESTING"] = True
    main.app.secret_key = "test-secret"
    test_client = main.app.test_client()
    with test_client.session_transaction() as session:
        session["user"] = {"email": USER_EMAIL, "name": "Lead"}
    return test_client


@pytest.fixture
def hosted(monkeypatch):
    """The hosted service: no manual booking in this process."""
    monkeypatch.setattr(main, "MANUAL_BOOKING_ENABLED", False)


def test_a_request_makes_a_user_due_regardless_of_interval(registered_user):
    from datetime import datetime
    from test_scan_frequency import record_scan_at

    user_manager.update_preferences(USER_EMAIL, {"scan_frequency_hours": 24})
    record_scan_at(USER_EMAIL, datetime.utcnow())
    assert tasks.user_is_due(USER_EMAIL) is False

    tasks.request_scan(USER_EMAIL)

    assert tasks.user_is_due(USER_EMAIL) is True


def test_requests_do_not_stack(registered_user):
    tasks.request_scan(USER_EMAIL)
    tasks.request_scan(USER_EMAIL)

    from sqlalchemy import select

    from db import SessionLocal
    from models import ScanRequest

    with SessionLocal() as db:
        assert len(db.execute(select(ScanRequest)).scalars().all()) == 1


def test_a_completed_scan_clears_the_request(monkeypatch, registered_user):
    from test_tasks import FakeAirtable

    tasks.request_scan(USER_EMAIL)
    monkeypatch.setattr(tasks, "create_airtable_client", lambda key: FakeAirtable())
    monkeypatch.setattr(tasks, "send_conflict_email", lambda *a, **k: None)
    monkeypatch.setattr(tasks.book_sessions, "delay", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "dispatch_booking", lambda *a, **k: None)

    tasks.scan_user(USER_EMAIL)

    assert tasks.has_pending_request(USER_EMAIL) is False


def test_the_button_triggers_the_scan_job(client, hosted, monkeypatch):
    called = {}
    monkeypatch.setattr(render_api, "trigger_scan_job",
                        lambda: (called.setdefault("hit", True), (True, "Scan started."))[1])

    response = client.post("/auto/run")

    assert response.status_code == 302
    assert called.get("hit") is True


def test_the_web_process_never_books_it_itself(client, hosted, monkeypatch):
    """The whole point: no Chrome in this container."""
    monkeypatch.setattr(render_api, "trigger_scan_job", lambda: (True, "Scan started."))
    monkeypatch.setattr(main, "dispatch_scan",
                        lambda email: pytest.fail("must not scan in the web process"))

    client.post("/auto/run")


def test_a_failed_trigger_still_leaves_the_request_standing(client, hosted, monkeypatch):
    """Render being unreachable must degrade to 'the next check picks it up'."""
    monkeypatch.setattr(render_api, "trigger_scan_job", lambda: (False, "Could not start a run just now."))

    client.post("/auto/run")

    assert tasks.has_pending_request(USER_EMAIL) is True


def test_an_in_flight_booking_is_not_cancelled(client, hosted, monkeypatch):
    """Triggering cancels any run in progress, which could strand a filed ticket."""
    monkeypatch.setattr(render_api, "trigger_scan_job",
                        lambda: pytest.fail("must not cancel a booking already running"))

    with tasks.booking_slot(wait_seconds=0):
        response = client.post("/auto/run")

    assert response.status_code == 302
    assert tasks.has_pending_request(USER_EMAIL) is True


def test_the_desktop_build_still_scans_in_process(client, monkeypatch):
    monkeypatch.setattr(main, "MANUAL_BOOKING_ENABLED", True)
    monkeypatch.setattr(render_api, "trigger_scan_job",
                        lambda: pytest.fail("the desktop build has no Render job to call"))
    scanned = []
    monkeypatch.setattr(main, "dispatch_scan", lambda email: scanned.append(email))

    client.post("/auto/run")

    import time
    for _ in range(50):
        if scanned:
            break
        time.sleep(0.01)
    assert scanned == [USER_EMAIL]


def test_unconfigured_deployments_say_so(monkeypatch):
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    monkeypatch.delenv("RENDER_CRON_JOB_ID", raising=False)

    assert render_api.is_configured() is False
    ok, message = render_api.trigger_scan_job()
    assert ok is False
    assert "scheduled check" in message


def test_a_successful_trigger_posts_to_the_right_place(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "rnd_secret")
    monkeypatch.setenv("RENDER_CRON_JOB_ID", "crn-abc123")
    seen = {}

    class FakeResponse:
        status_code = 200
        text = ""

    def fake_post(url, headers=None, timeout=None):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        return FakeResponse()

    monkeypatch.setattr(render_api.requests, "post", fake_post)

    ok, _ = render_api.trigger_scan_job()

    assert ok is True
    assert seen["url"].endswith("/cron-jobs/crn-abc123/runs")
    assert seen["auth"] == "Bearer rnd_secret"


def test_an_api_refusal_is_reported_as_a_deferral(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "rnd_secret")
    monkeypatch.setenv("RENDER_CRON_JOB_ID", "crn-abc123")

    class FakeResponse:
        status_code = 401
        text = "unauthorized"

    monkeypatch.setattr(render_api.requests, "post", lambda *a, **k: FakeResponse())

    ok, message = render_api.trigger_scan_job()

    assert ok is False
    assert "next scheduled check" in message
