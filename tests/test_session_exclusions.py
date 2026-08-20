"""Removing a session on the dashboard has to stick.

It used to last only until the page reloaded, so a session someone deliberately
took out came back and was booked by the next scheduled run.
"""
from datetime import datetime, timedelta, timezone

import pytest

import tasks
from user_profiles import user_manager

from test_tasks import BASE_TIME, FakeAirtable, FakeSession, USER_EMAIL


@pytest.fixture
def registered_user():
    user_manager.upsert_user(USER_EMAIL, name="Lead")
    user_manager.save_profile(USER_EMAIL, {
        "airtable_api_key": "patFakeKey",
        "servicenow_password": "hunter2",
        "totp_secret": "JBSWY3DPEHPK3PXP",
        "preferences": {"auto_booking_enabled": True},
    })
    return USER_EMAIL


@pytest.fixture
def wired(monkeypatch, registered_user):
    airtable = FakeAirtable()
    calls = {"booked_ids": [], "airtable": airtable}

    monkeypatch.setattr(tasks, "create_airtable_client", lambda key: airtable)
    monkeypatch.setattr(tasks, "send_conflict_email", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "send_booking_summary_email", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "dispatch_booking",
                        lambda email, ids, manual=False: calls["booked_ids"].append(list(ids)))
    return calls


def test_removing_and_restoring_round_trips(registered_user):
    assert tasks.excluded_session_ids(USER_EMAIL) == set()

    tasks.set_session_excluded(USER_EMAIL, "recClean1", True, title="Clean One",
                               school="Nakasuk School")
    assert tasks.excluded_session_ids(USER_EMAIL) == {"recClean1"}

    tasks.set_session_excluded(USER_EMAIL, "recClean1", False)
    assert tasks.excluded_session_ids(USER_EMAIL) == set()


def test_removing_twice_does_not_duplicate(registered_user):
    tasks.set_session_excluded(USER_EMAIL, "recClean1", True, title="Clean One")
    tasks.set_session_excluded(USER_EMAIL, "recClean1", True, title="Clean One")

    assert tasks.excluded_session_ids(USER_EMAIL) == {"recClean1"}
    assert len(tasks.outstanding_exclusions(USER_EMAIL)) <= 1


def test_restoring_something_never_removed_is_harmless(registered_user):
    assert tasks.set_session_excluded(USER_EMAIL, "recNeverSeen", False) is True
    assert tasks.excluded_session_ids(USER_EMAIL) == set()


def test_a_removed_session_is_not_booked_by_a_scheduled_scan(wired):
    tasks.set_session_excluded(USER_EMAIL, "recClean1", True, title="Clean One")

    tasks.scan_user(USER_EMAIL)

    assert wired["booked_ids"] == [["recClean2"]]


def test_it_still_is_not_booked_on_the_next_run(wired):
    """The whole point: this must not come back tomorrow."""
    tasks.set_session_excluded(USER_EMAIL, "recClean1", True, title="Clean One")

    tasks.scan_user(USER_EMAIL)
    tasks.scan_user(USER_EMAIL)

    assert wired["booked_ids"] == [["recClean2"], ["recClean2"]]


def test_putting_it_back_lets_it_book_again(wired):
    tasks.set_session_excluded(USER_EMAIL, "recClean1", True, title="Clean One")
    tasks.scan_user(USER_EMAIL)
    tasks.set_session_excluded(USER_EMAIL, "recClean1", False)
    tasks.scan_user(USER_EMAIL)

    assert wired["booked_ids"] == [["recClean2"], ["recClean1", "recClean2"]]


def test_booking_refuses_a_session_removed_after_the_scan(monkeypatch, wired):
    """A removal between the scan and the booking run still has to win."""
    handled = {}

    def fake_handler(send_to_gn, *args, **kwargs):
        handled["ids"] = [s.s_id for s in send_to_gn]
        return {"successful_sessions": [], "failed_sessions": []}

    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", fake_handler)
    tasks.set_session_excluded(USER_EMAIL, "recClean1", True, title="Clean One")

    tasks.book_sessions(USER_EMAIL, ["recClean1", "recClean2"])

    assert handled["ids"] == ["recClean2"]


def test_a_removed_conflict_stops_being_emailed_about(monkeypatch, wired):
    emails = []
    monkeypatch.setattr(tasks, "send_conflict_email",
                        lambda email, sessions: emails.append(list(sessions)))

    tasks.set_session_excluded(USER_EMAIL, "recConflict", True, title="Conflicted")
    tasks.scan_user(USER_EMAIL)

    assert emails == []


def test_outstanding_exclusions_lists_upcoming_ones(registered_user):
    start = (BASE_TIME + timedelta(days=3)).replace(tzinfo=None)
    tasks.set_session_excluded(USER_EMAIL, "recClean1", True, title="Clean One",
                               school="Nakasuk School", start_time=start)

    listed = tasks.outstanding_exclusions(USER_EMAIL)

    assert [row["session_id"] for row in listed] == ["recClean1"]
    assert listed[0]["title"] == "Clean One"
    assert listed[0]["school"] == "Nakasuk School"


def test_exclusions_in_the_past_drop_off_the_list(registered_user):
    """A session that has happened can never be booked, so it stops being reported."""
    tasks.set_session_excluded(USER_EMAIL, "recOld", True, title="Last Month",
                               start_time=datetime.utcnow() - timedelta(days=30))

    assert tasks.outstanding_exclusions(USER_EMAIL) == []
    # Still excluded, just no longer worth mentioning.
    assert tasks.excluded_session_ids(USER_EMAIL) == {"recOld"}


def test_exclusions_are_per_user(registered_user):
    other = "colleague@takingitglobal.org"
    user_manager.upsert_user(other, name="Colleague")
    user_manager.save_profile(other, {
        "airtable_api_key": "patOther", "servicenow_password": "x",
        "totp_secret": "JBSWY3DPEHPK3PXP", "preferences": {},
    })

    tasks.set_session_excluded(USER_EMAIL, "recClean1", True, title="Clean One")

    assert tasks.excluded_session_ids(USER_EMAIL) == {"recClean1"}
    assert tasks.excluded_session_ids(other) == set()
