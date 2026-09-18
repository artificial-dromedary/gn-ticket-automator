"""Regression tests for the code-review fixes: things that were silently wrong."""
from datetime import timezone

import pytest

import airtable_integration
from airtable_integration import (AirtableSession, parse_airtable_datetime, quote,
                                  zoom_meeting_id)
import main
import ticket_submission_log


# --- Airtable formulas ------------------------------------------------------

def test_a_school_name_with_an_apostrophe_does_not_break_the_formula(monkeypatch):
    """"St. John's" used to end the string literal early and fail the whole query."""
    seen = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"records": []}

    def fake_get(self, url, headers=None, params=None, timeout=None):
        seen["formula"] = params["filterByFormula"]
        return FakeResponse()

    monkeypatch.setattr(airtable_integration.requests.Session, "get", fake_get)
    airtable_integration.AirtableIntegration("patFake").get_all_sessions_for_schools(
        ["St. John's School", "Nakasuk School"], status_filters=["Booked"])

    assert "{School Name Text} = 'St. John\\'s School'" in seen["formula"]
    assert "{School Name Text} = 'Nakasuk School'" in seen["formula"]


def test_quote_escapes_backslashes_before_quotes():
    assert quote("a\\b'c") == "'a\\\\b\\'c'"


# --- Dates and Zoom ---------------------------------------------------------

def test_a_session_with_no_date_has_no_start_time():
    """It used to default to "now", which read as "starts within 12 hours"."""
    session = AirtableSession({"id": "rec1", "fields": {"Session Title Text": "No date"}})
    assert session.start_time is None
    assert "no date" in str(session)


def test_airtable_dates_are_read_as_aware_utc():
    parsed = parse_airtable_datetime("2026-03-04T18:00:00.000Z")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed.astimezone(timezone.utc).hour == 18
    assert parse_airtable_datetime("not a date") is None
    assert parse_airtable_datetime("") is None


@pytest.mark.parametrize("link, expected", [
    ("https://us02web.zoom.us/j/81234567890?pwd=abc12345DEF", "81234567890"),
    ("https://zoom.us/j/8123456789", "8123456789"),
    ("Meeting ID: 812 3456 7890", None),
    ("https://example.com/no-zoom-here", None),
    ("", None),
])
def test_zoom_meeting_id_is_parsed_not_sliced(link, expected):
    """The last eleven characters of a link with ?pwd= used to become the SIP address."""
    assert zoom_meeting_id(link) == expected


# --- One ticket history -----------------------------------------------------

def test_the_dashboard_and_the_scheduled_run_share_one_ticket_history():
    import tasks
    assert main.ticket_log is tasks.ticket_log is ticket_submission_log.ticket_log
    assert main.ticket_log.retention_days == ticket_submission_log.DEFAULT_RETENTION_DAYS


# --- Web app guards ---------------------------------------------------------

@pytest.fixture
def client():
    main.app.config["TESTING"] = True
    main.app.secret_key = "test-secret"
    test_client = main.app.test_client()
    with test_client.session_transaction() as session:
        session["user"] = {"email": "lead@takingitglobal.org", "name": "Lead"}
    return test_client


def test_a_cross_site_post_is_refused(client):
    response = client.post("/gn_ticket/set_lookahead", json={"window_future_days": 7},
                           headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403

    response = client.post("/gn_ticket/set_lookahead", json={"window_future_days": 7},
                           headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_a_same_site_post_goes_through(client):
    response = client.post("/gn_ticket/set_lookahead", json={"window_future_days": 7},
                           headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"})
    assert response.status_code == 200


def test_progress_is_only_readable_by_the_person_who_started_it(client):
    main.start_progress("gn_booking_test", "someone.else@takingitglobal.org")
    main.set_progress("gn_booking_test", "hello")

    assert client.get("/progress-status/gn_booking_test").status_code == 404

    main.start_progress("gn_booking_mine", "lead@takingitglobal.org")
    main.set_progress("gn_booking_mine", "hello")
    response = client.get("/progress-status/gn_booking_mine")
    assert response.status_code == 200
    assert response.get_json()["entries"][0]["message"] == "hello"


def test_progress_for_a_run_nobody_is_watching_is_dropped():
    """The scheduled run passes no id; it must not accumulate entries under None."""
    before = dict(main.progress_store)
    main.set_progress(None, "quiet")
    assert dict(main.progress_store) == before
