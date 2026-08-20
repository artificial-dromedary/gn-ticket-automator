"""Manual booking is a deployment choice.

It drives Chrome inside the web process, which a 512 MB hosted instance cannot
survive. It stays available in the desktop build, where there is memory for it,
and is switched off on the hosted service — which books on a schedule instead.
"""
import pytest

import main


@pytest.fixture
def client():
    main.app.config["TESTING"] = True
    main.app.secret_key = "test-secret"
    test_client = main.app.test_client()
    with test_client.session_transaction() as session:
        session["user"] = {"email": "lead@takingitglobal.org", "name": "Lead"}
    return test_client


def test_it_is_on_by_default():
    """The desktop build sets nothing and must keep working as before."""
    import importlib
    import os

    os.environ.pop("GN_ENABLE_MANUAL_BOOKING", None)
    assert importlib.reload(main).MANUAL_BOOKING_ENABLED is True


def test_booking_is_refused_when_disabled(client, monkeypatch):
    monkeypatch.setattr(main, "MANUAL_BOOKING_ENABLED", False)
    monkeypatch.setattr(main.user_manager, "load_profile",
                        lambda email: pytest.fail("must not reach the booking path"))

    response = client.post("/gn_ticket/book_sessions", data={})

    assert response.status_code == 403
    assert b"desktop app" in response.data


def test_the_button_is_hidden_when_disabled():
    from flask import render_template

    context = dict(all_sessions=[], submitted_ticket_log=[], latest_conflicts=[],
                   emailed_conflict_ids=set(), user={"name": "Lead", "email": "l@x.org"},
                   buffer_before=10, buffer_after=10, window_past_days=14,
                   window_future_days=90, auto_booking_enabled=True,
                   scan_frequency_hours=24, scan_frequency_choices=(1, 5, 12, 24),
                   auto_scan_time="once a day", latest_scan=None, booking_busy_since=None)

    with main.app.test_request_context("/gn_ticket"):
        off = render_template("gn.html", manual_booking_enabled=False, **context)
        on = render_template("gn.html", manual_booking_enabled=True, **context)

    assert "Book displayed sessions with GN" not in off
    assert "Booking runs on a schedule here" in off
    assert "Book displayed sessions with GN" in on


def test_the_frequency_dropdown_offers_every_interval():
    from flask import render_template

    with main.app.test_request_context("/gn_ticket"):
        html = render_template(
            "gn.html", all_sessions=[], submitted_ticket_log=[], latest_conflicts=[],
            emailed_conflict_ids=set(), user={"name": "Lead", "email": "l@x.org"},
            buffer_before=10, buffer_after=10, window_past_days=14, window_future_days=90,
            auto_booking_enabled=True, scan_frequency_hours=5,
            scan_frequency_choices=(1, 5, 12, 24), manual_booking_enabled=False,
            auto_scan_time="every 5 hours", latest_scan=None, booking_busy_since=None)

    assert "Every hour" in html
    assert "Once a day" in html
    assert 'value="5" selected' in html
