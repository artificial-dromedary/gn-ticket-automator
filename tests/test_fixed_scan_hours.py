"""Fixed scan hours: everyone at 7am and 5pm Eastern, weekdays.

Render's cron schedule is UTC, so the job fires at both UTC hours each Eastern
time can fall on, and only the tick that lands on the local hour scans. These
pin that down, plus the dashboard no longer offering a per-user interval.
"""
from datetime import timedelta

import pytest

import main
import tasks
from user_profiles import user_manager

from test_scan_frequency import eastern, record_scan_at, set_frequency
from test_tasks import USER_EMAIL, registered_user  # noqa: F401


@pytest.fixture
def fixed_hours(monkeypatch):
    monkeypatch.setattr(tasks, "FIXED_SCAN_HOURS", (7, 17))
    monkeypatch.setattr(tasks, "SCAN_WEEKDAYS_ONLY", True)


# 2026-07-15 is a Wednesday in daylight time; 2026-01-14 a Wednesday in standard.

def test_hours_are_parsed_and_bad_entries_dropped():
    assert tasks._parse_scan_hours("17, 7") == (7, 17)
    assert tasks._parse_scan_hours("") == ()
    assert tasks._parse_scan_hours("7,noon,25") == (7,)


@pytest.mark.parametrize("local", ["2026-07-15 07:00", "2026-07-15 17:00",
                                   "2026-01-14 07:00", "2026-01-14 17:00"])
def test_due_at_seven_and_five_in_summer_and_winter(fixed_hours, registered_user, local):
    record_scan_at(USER_EMAIL, eastern("2026-07-01 07:00") - timedelta(days=200))

    assert tasks.user_is_due(USER_EMAIL, now=eastern(local)) is True


@pytest.mark.parametrize("local", ["2026-07-15 08:00", "2026-07-15 18:00",
                                   "2026-01-14 06:00", "2026-01-14 16:00"])
def test_the_other_daylight_saving_tick_scans_nobody(fixed_hours, registered_user, local):
    """The cron fires at 11, 12, 21 and 22 UTC; the off-hour pair must be a no-op."""
    record_scan_at(USER_EMAIL, eastern("2026-07-01 07:00") - timedelta(days=200))

    assert tasks.user_is_due(USER_EMAIL, now=eastern(local)) is False


def test_a_never_scanned_user_still_waits_for_a_fixed_hour(fixed_hours, registered_user):
    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 12:00")) is False
    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 17:00")) is True


def test_not_due_twice_in_the_same_slot(fixed_hours, registered_user):
    record_scan_at(USER_EMAIL, eastern("2026-07-15 07:02"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 07:30")) is False
    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 17:00")) is True


def test_an_earlier_on_demand_scan_does_not_skip_the_slot(fixed_hours, registered_user):
    record_scan_at(USER_EMAIL, eastern("2026-07-15 06:30"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 07:00")) is True


def test_the_per_user_interval_is_ignored(fixed_hours, registered_user):
    set_frequency(USER_EMAIL, 1)
    record_scan_at(USER_EMAIL, eastern("2026-07-15 07:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 12:00")) is False


def test_weekends_are_skipped(fixed_hours, registered_user):
    record_scan_at(USER_EMAIL, eastern("2026-07-10 17:00"))  # Friday

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-11 07:00")) is False  # Saturday
    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-12 17:00")) is False  # Sunday
    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-13 07:00")) is True   # Monday


def test_asking_for_a_scan_works_at_any_hour(fixed_hours, registered_user):
    record_scan_at(USER_EMAIL, eastern("2026-07-11 07:00"))
    tasks.request_scan(USER_EMAIL)

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-11 14:00")) is True


def test_the_label(fixed_hours):
    label = tasks.auto_scan_time_label(24)
    assert label.startswith("weekdays at 7am and 5pm ")


def test_the_label_without_weekdays_only(monkeypatch):
    monkeypatch.setattr(tasks, "FIXED_SCAN_HOURS", (7, 12, 17))
    monkeypatch.setattr(tasks, "SCAN_WEEKDAYS_ONLY", False)
    assert tasks.auto_scan_time_label().startswith("every day at 7am, 12pm and 5pm ")


@pytest.fixture
def client(registered_user):
    main.app.config["TESTING"] = True
    main.app.secret_key = "test-secret"
    test_client = main.app.test_client()
    with test_client.session_transaction() as session:
        session["user"] = {"email": USER_EMAIL, "name": "Lead"}
    return test_client


def test_saving_settings_without_the_picker_keeps_the_stored_interval(client):
    set_frequency(USER_EMAIL, 12)

    client.post("/preferences", data={"auto_booking_enabled": "yes"})

    assert user_manager.get_preferences(USER_EMAIL)["scan_frequency_hours"] == 12


def test_the_picker_is_hidden_when_there_are_no_choices():
    from flask import render_template

    context = dict(all_sessions=[], submitted_ticket_log=[], latest_conflicts=[],
                   emailed_conflict_ids=set(), user={"name": "Lead", "email": "l@x.org"},
                   buffer_before=10, buffer_after=10, window_past_days=14,
                   window_future_days=90, auto_booking_enabled=True,
                   scan_frequency_hours=24, auto_scan_time="weekdays at 7am and 5pm EST",
                   latest_scan=None, booking_busy_since=None, scan_notice=None,
                   manual_booking_enabled=False)

    with main.app.test_request_context("/gn_ticket"):
        fixed = render_template("gn.html", scan_frequency_choices=(), **context)
        intervals = render_template("gn.html", scan_frequency_choices=(1, 4, 12, 24), **context)

    assert 'name="scan_frequency_hours"' not in fixed
    assert "Runs weekdays at 7am and 5pm EST" in fixed
    assert 'name="scan_frequency_hours"' in intervals
