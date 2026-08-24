"""Per-user scan frequency.

The cron job fires hourly and asks each opted-in user whether their chosen
interval has elapsed, so 1/5/12/24 hours is a dashboard setting rather than a
schedule change. Every interval is counted out from 9am Eastern rather than from
whenever the last scan happened to land, so the hours it uses are the same for
everyone on it.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import tasks
from user_profiles import (DEFAULT_SCAN_FREQUENCY_HOURS, RETIRED_SCAN_FREQUENCIES,
                           SCAN_FREQUENCY_CHOICES, _retire_removed_scan_frequencies,
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


EASTERN = ZoneInfo("America/New_York")


def eastern(local):
    """A wall-clock time in Eastern, as the naive UTC the scan code works in."""
    moment = datetime.strptime(local, "%Y-%m-%d %H:%M").replace(tzinfo=EASTERN)
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def test_the_offered_intervals():
    assert SCAN_FREQUENCY_CHOICES == (1, 4, 12, 24)
    assert DEFAULT_SCAN_FREQUENCY_HOURS == 24


def test_every_offered_interval_divides_the_day():
    """Anchoring only spaces slots evenly if the interval fits a day a whole number
    of times. 5 hours did not, which is why it was retired."""
    for hours in SCAN_FREQUENCY_CHOICES:
        assert 24 % hours == 0
        assert len(tasks.scan_hours(hours)) == 24 // hours


@pytest.mark.parametrize("value,expected", [
    (1, 1), ("4", 4), (12, 12), ("24", 24),
    (3, 24), ("banana", 24), (None, 24), (0, 24), (-1, 24),
    (5, 24),  # retired: no longer an answer a form post can give.
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
    set_frequency(USER_EMAIL, 4)
    record_scan_at(USER_EMAIL, eastern("2026-07-15 09:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 13:00")) is True


def test_a_shorter_interval_comes_due_sooner(registered_user):
    """The same elapsed time reads differently at 1 hour and at 24."""
    # Pinned off the daily hour: at 9am a daily user is due whatever the elapsed time.
    now = eastern("2026-07-15 14:00")
    record_scan_at(USER_EMAIL, now - timedelta(hours=2))

    set_frequency(USER_EMAIL, 1)
    assert tasks.user_is_due(USER_EMAIL, now=now) is True

    set_frequency(USER_EMAIL, 24)
    assert tasks.user_is_due(USER_EMAIL, now=now) is False


def test_an_hourly_user_is_not_pushed_to_two_hours_by_drift(registered_user):
    """Cron fires on the hour; a scan that began seconds late must still count."""
    set_frequency(USER_EMAIL, 1)
    record_scan_at(USER_EMAIL, eastern("2026-07-15 09:00") + timedelta(seconds=40))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 10:00")) is True


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
    assert tasks.auto_scan_time_label(4).startswith("every 4 hours, from 9am")
    assert tasks.auto_scan_time_label(12).startswith("at 9am and 9pm")
    assert tasks.auto_scan_time_label(24).startswith("once a day, at 9am")


# The hours each interval lands on. The cron job still fires hourly — a user set
# to "every hour" needs it to — so what pins a scan to the morning is which of
# those 24 ticks the user's interval answers "due" on.

def test_a_daily_user_is_due_at_nine_eastern(registered_user):
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, eastern("2026-07-14 09:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 09:00")) is True


def test_a_daily_user_is_not_due_in_the_middle_of_the_night(registered_user):
    """The whole point: 3am is a tick like any other, and daily users skip it."""
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, eastern("2026-07-14 09:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 03:00")) is False


def test_a_daily_user_is_not_due_again_later_the_same_day(registered_user):
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, eastern("2026-07-15 09:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 10:00")) is False
    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 23:00")) is False


def test_an_afternoon_scan_does_not_drag_tomorrow_into_the_afternoon(registered_user):
    """Pressing "Run scan now" at 4pm must not move the daily scan to 4pm."""
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, eastern("2026-07-14 16:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 09:00")) is True


def test_nine_eastern_survives_daylight_saving(registered_user):
    """Same wall-clock hour in winter, an hour later in UTC. Both are 9am to a user."""
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, eastern("2026-01-14 09:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-01-15 08:00")) is False
    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-01-15 09:00")) is True


def test_a_missed_morning_is_caught_up_rather_than_skipped_for_a_day(registered_user):
    """A failed 9am run should cost hours, not until tomorrow morning."""
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, eastern("2026-07-14 09:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 10:00")) is True


def test_a_catch_up_does_not_move_the_next_morning(registered_user):
    """Tomorrow is anchored to the clock, not to whenever the catch-up ran."""
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, eastern("2026-07-15 11:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-16 09:00")) is True


def test_a_four_hour_interval_fills_the_day_from_nine(registered_user):
    """Six evenly spaced slots, counted out from the anchor and wrapping midnight."""
    assert tasks.scan_hours(4) == (1, 5, 9, 13, 17, 21)

    set_frequency(USER_EMAIL, 4)
    record_scan_at(USER_EMAIL, eastern("2026-07-15 21:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 23:00")) is False
    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-16 01:00")) is True


def test_a_twice_daily_user_gets_nine_and_nine(registered_user):
    assert tasks.scan_hours(12) == (9, 21)

    set_frequency(USER_EMAIL, 12)
    record_scan_at(USER_EMAIL, eastern("2026-07-15 09:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 15:00")) is False
    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 21:00")) is True


def test_an_hourly_user_still_answers_to_every_tick(registered_user):
    """Anchoring must not thin out the interval that wants all 24 hours."""
    assert len(tasks.scan_hours(1)) == 24

    set_frequency(USER_EMAIL, 1)
    record_scan_at(USER_EMAIL, eastern("2026-07-15 03:00"))

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 04:00")) is True


def test_asking_for_a_scan_still_beats_the_hour(registered_user):
    """An explicit request is not made to wait until the morning."""
    set_frequency(USER_EMAIL, 24)
    record_scan_at(USER_EMAIL, eastern("2026-07-15 09:00"))
    tasks.request_scan(USER_EMAIL)

    assert tasks.user_is_due(USER_EMAIL, now=eastern("2026-07-15 16:00")) is True


def store_frequency_directly(email, hours):
    """Write a raw value, bypassing the normalising the dashboard does on save."""
    from sqlalchemy import select

    from db import SessionLocal
    from models import User, UserPreference

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        prefs = db.execute(
            select(UserPreference).where(UserPreference.user_id == user.id)
        ).scalar_one()
        prefs.scan_frequency_hours = hours
        db.commit()


def test_a_retired_interval_is_moved_to_what_replaced_it(registered_user):
    """Left alone, a stored 5 would select nothing in the dashboard's dropdown,
    and the next save would silently pick the first option for the user."""
    assert RETIRED_SCAN_FREQUENCIES == {5: 4}
    store_frequency_directly(USER_EMAIL, 5)

    _retire_removed_scan_frequencies()

    assert user_manager.get_preferences(USER_EMAIL)["scan_frequency_hours"] == 4


def test_moving_retired_intervals_leaves_the_offered_ones_alone(registered_user):
    for hours in SCAN_FREQUENCY_CHOICES:
        store_frequency_directly(USER_EMAIL, hours)

        _retire_removed_scan_frequencies()

        assert user_manager.get_preferences(USER_EMAIL)["scan_frequency_hours"] == hours
