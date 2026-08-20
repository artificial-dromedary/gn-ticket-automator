"""Per-user scan frequency.

The cron job fires hourly and asks each opted-in user whether their chosen
interval has elapsed, so 1/5/12/24 hours is a dashboard setting rather than a
schedule change.
"""
from datetime import datetime, timedelta

import pytest

import tasks
from user_profiles import (DEFAULT_SCAN_FREQUENCY_HOURS, SCAN_FREQUENCY_CHOICES,
                           normalize_scan_frequency, user_manager)

from test_tasks import USER_EMAIL, registered_user  # noqa: F401


def set_frequency(email, hours):
    user_manager.update_preferences(email, {"scan_frequency_hours": hours})


def record_scan_at(email, when):
    """Stand in for a completed scan at a given moment."""
    from sqlalchemy import select

    from db import SessionLocal
    from models import ScanResult, User

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        db.add(ScanResult(user_id=user.id, scanned_at=when, summary="{}"))
        db.commit()


def test_the_offered_intervals():
    assert SCAN_FREQUENCY_CHOICES == (1, 5, 12, 24)
    assert DEFAULT_SCAN_FREQUENCY_HOURS == 24


@pytest.mark.parametrize("value,expected", [
    (1, 1), ("5", 5), (12, 12), ("24", 24),
    (3, 24), ("banana", 24), (None, 24), (0, 24), (-1, 24),
])
def test_only_offered_intervals_are_accepted(value, expected):
    """A hand-edited form post must not produce a two-minute scan loop."""
    assert normalize_scan_frequency(value) == expected


def test_a_user_never_scanned_is_due(registered_user):
    assert tasks.user_is_due(USER_EMAIL) is True


def test_a_user_scanned_just_now_is_not_due(registered_user):
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, datetime.utcnow())

    assert tasks.user_is_due(USER_EMAIL) is False


def test_a_user_becomes_due_once_the_interval_elapses(registered_user):
    set_frequency(USER_EMAIL, 5)
    record_scan_at(USER_EMAIL, datetime.utcnow() - timedelta(hours=5, minutes=1))

    assert tasks.user_is_due(USER_EMAIL) is True


def test_a_shorter_interval_comes_due_sooner(registered_user):
    """The same elapsed time reads differently at 1 hour and at 24."""
    record_scan_at(USER_EMAIL, datetime.utcnow() - timedelta(hours=2))

    set_frequency(USER_EMAIL, 1)
    assert tasks.user_is_due(USER_EMAIL) is True

    set_frequency(USER_EMAIL, 24)
    assert tasks.user_is_due(USER_EMAIL) is False


def test_an_hourly_user_is_not_pushed_to_two_hours_by_drift(registered_user):
    """Cron fires on the hour; a scan that began seconds late must still count."""
    set_frequency(USER_EMAIL, 1)
    record_scan_at(USER_EMAIL, datetime.utcnow() - timedelta(minutes=58))

    assert tasks.user_is_due(USER_EMAIL) is True


def test_the_scheduled_run_scans_only_who_is_due(monkeypatch, registered_user):
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, datetime.utcnow())

    scanned = []
    monkeypatch.setattr(tasks, "dispatch_scan", lambda email: scanned.append(email))

    tasks.run_scheduled_scan()

    assert scanned == []


def test_force_ignores_intervals(monkeypatch, registered_user):
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, datetime.utcnow())

    scanned = []
    monkeypatch.setattr(tasks, "dispatch_scan", lambda email: scanned.append(email))

    tasks.run_scheduled_scan(force=True)

    assert scanned == [USER_EMAIL]


def test_the_frequency_survives_a_round_trip(registered_user):
    set_frequency(USER_EMAIL, 12)

    assert user_manager.get_preferences(USER_EMAIL)["scan_frequency_hours"] == 12
    assert user_manager.load_profile(USER_EMAIL)["preferences"]["scan_frequency_hours"] == 12


def test_the_label_reads_naturally():
    assert tasks.auto_scan_time_label(1) == "every hour"
    assert tasks.auto_scan_time_label(5) == "every 5 hours"
    assert tasks.auto_scan_time_label(24) == "once a day"
