"""Candidate sessions must be upcoming ones.

A session that has already started cannot usefully be ticketed, so the Airtable
query filters from NOW() rather than from a window that reaches into the past.
The past window still governs ticket history and conflict lookups, which do need
to see backwards.
"""
import airtable_integration
from airtable_integration import AirtableIntegration


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"records": []}


def capture_filter(monkeypatch, call):
    """Run `call` and return the filterByFormula Airtable was sent."""
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["formula"] = params.get("filterByFormula", "")
        return FakeResponse()

    monkeypatch.setattr(airtable_integration.requests, "get", fake_get)
    call()
    return seen["formula"]


def test_candidates_start_from_now(monkeypatch):
    client = AirtableIntegration("patTest")

    formula = capture_filter(monkeypatch, lambda: client.get_booked_sessions(
        user_email="lead@takingitglobal.org", window_past_days=14, window_future_days=90))

    assert "IS_AFTER({Session Start Date/Time}, NOW())" in formula


def test_candidates_do_not_reach_into_the_past(monkeypatch):
    """The regression: a 14 day past window made yesterday's session a candidate."""
    client = AirtableIntegration("patTest")

    formula = capture_filter(monkeypatch, lambda: client.get_booked_sessions(
        user_email="lead@takingitglobal.org", window_past_days=14, window_future_days=90))

    assert "DATEADD(TODAY(), -14, 'day')" not in formula
    assert "-14" not in formula


def test_the_forward_window_still_applies(monkeypatch):
    """90 days means through the end of the 90th day, so the cutoff is day 91."""
    client = AirtableIntegration("patTest")

    formula = capture_filter(monkeypatch, lambda: client.get_booked_sessions(
        user_email="lead@takingitglobal.org", window_past_days=14, window_future_days=90))

    assert "DATEADD(TODAY(), 91, 'day')" in formula


def test_today_still_matches_the_rest_of_today(monkeypatch):
    """The regression: a 0 day window asked for sessions before this morning, so the
    "Today" stop found nothing and the scheduled run booked nothing."""
    client = AirtableIntegration("patTest")

    formula = capture_filter(monkeypatch, lambda: client.get_booked_sessions(
        user_email="lead@takingitglobal.org", window_past_days=14, window_future_days=0))

    assert "DATEADD(TODAY(), 1, 'day')" in formula
    assert "DATEADD(TODAY(), 0, 'day')" not in formula


def test_the_conflict_lookup_uses_the_same_horizon(monkeypatch):
    client = AirtableIntegration("patTest")

    formula = capture_filter(monkeypatch, lambda: client.get_all_sessions_for_schools(
        ["Nakasuk School"], status_filters=["Booked"], window_past_days=14,
        window_future_days=0))

    assert "DATEADD(TODAY(), 1, 'day')" in formula


def test_conflict_lookups_still_look_backwards(monkeypatch):
    """Conflict checking is a different question and keeps its past window."""
    client = AirtableIntegration("patTest")

    formula = capture_filter(monkeypatch, lambda: client.get_all_sessions_for_schools(
        ["Nakasuk School"], status_filters=["Booked"], window_past_days=14, window_future_days=90))

    assert "DATEADD(TODAY(), -14, 'day')" in formula


def test_the_other_candidate_filters_survive(monkeypatch):
    client = AirtableIntegration("patTest")

    formula = capture_filter(monkeypatch, lambda: client.get_booked_sessions(
        user_email="lead@takingitglobal.org"))

    assert "{School Lead Email} = 'lead@takingitglobal.org'" in formula
    assert "FIND('NU', {School P/T}) > 0" in formula
    assert "NOT({GN Ticket Requested} = TRUE())" in formula
