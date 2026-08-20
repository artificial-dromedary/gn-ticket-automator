"""Sessions starting too soon are held back like a conflict.

The GN cannot action a request filed hours before the session, so sending one is
noise. These are reported instead, and can still be booked by hand.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conflict import LAST_MINUTE_HOURS, check_for_time_conflicts


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class DummySession:
    def __init__(self, s_id, title, school, start_time, length=60,
                 status="Booked", teacher="Teacher", gn_ticket_requested=False):
        self.s_id = s_id
        self.title = title
        self.school = school
        self.start_time = start_time
        self.length = length
        self.status = status
        self.teacher = teacher
        self.teacher_email = "teacher@example.com"
        self.gn_ticket_requested = gn_ticket_requested
        self.created_at = ""


def check(sessions, existing=None, now=NOW):
    return check_for_time_conflicts(sessions, existing or [], [], now=now)


def test_default_window_is_twelve_hours():
    assert LAST_MINUTE_HOURS == 12


@pytest.mark.parametrize("hours_away", [0.5, 3, 11, 11.9])
def test_sessions_inside_the_window_are_flagged(hours_away):
    session = DummySession("rec1", "Soon", "School A", NOW + timedelta(hours=hours_away))
    result = check([session])[0]

    assert result.is_conflict is True
    assert result.conflict_type == "last_minute"
    assert "12 hours" in result.conflict_details


@pytest.mark.parametrize("hours_away", [12.1, 24, 200])
def test_sessions_beyond_the_window_are_left_alone(hours_away):
    session = DummySession("rec1", "Later", "School A", NOW + timedelta(hours=hours_away))
    result = check([session])[0]

    assert result.is_conflict is False
    assert result.conflict_type is None


def test_a_session_already_underway_counts_as_last_minute():
    session = DummySession("rec1", "Started", "School A", NOW - timedelta(hours=2))
    assert check([session])[0].conflict_type == "last_minute"


def test_last_minute_wins_over_a_time_conflict():
    """No point working out who to email about a clash that is hours away."""
    soon = NOW + timedelta(hours=3)
    candidate = DummySession("rec1", "Soon", "School A", soon)
    existing = DummySession("rec2", "Already Ticketed", "School A", soon,
                            gn_ticket_requested=True)

    result = check([candidate], [existing])[0]

    assert result.conflict_type == "last_minute"


def test_it_does_not_drag_other_sessions_down_with_it():
    soon = DummySession("rec1", "Soon", "School A", NOW + timedelta(hours=2))
    later = DummySession("rec2", "Later", "School B", NOW + timedelta(days=5))

    results = {s.s_id: s for s in check([soon, later])}

    assert results["rec1"].is_conflict is True
    assert results["rec2"].is_conflict is False


def test_the_window_is_configurable():
    session = DummySession("rec1", "Tomorrow", "School A", NOW + timedelta(hours=20))

    assert check_for_time_conflicts([session], [], [], now=NOW,
                                    last_minute_hours=24)[0].conflict_type == "last_minute"
    assert check_for_time_conflicts([session], [], [], now=NOW,
                                    last_minute_hours=12)[0].is_conflict is False


def test_zero_hours_turns_the_rule_off():
    session = DummySession("rec1", "Imminent", "School A", NOW + timedelta(minutes=5))
    assert check_for_time_conflicts([session], [], [], now=NOW,
                                    last_minute_hours=0)[0].is_conflict is False
