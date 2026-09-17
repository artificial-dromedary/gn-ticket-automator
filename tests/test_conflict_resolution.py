"""Marking a clash resolved on the dashboard has to stick, and tell the host.

The usual answer to two sessions overlapping is that one class joins by Zoom while
the other keeps the Cisco machine. Nothing about that changes the times in Airtable,
so without a record of the decision every later scan finds the same overlap and holds
the session back again.
"""
from datetime import timedelta

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
    monkeypatch.setattr(tasks.book_sessions, "delay",
                        lambda email, ids, manual=False: calls["booked_ids"].append(list(ids)))
    return calls


def _clashing(session_id="recConflict", other_id="recExisting"):
    session = FakeSession(session_id, "Conflicted", "Qiqirtaq School",
                          BASE_TIME + timedelta(days=2))
    session.is_conflict = True
    session.conflict_type = "time"
    session.conflict_details = "Conflicts with previously booked session 'Already Ticketed'."
    session.conflict_other_id = other_id
    return session


def test_resolving_round_trips(registered_user):
    assert tasks.resolved_conflict_pairs(USER_EMAIL) == {}

    tasks.record_conflict_resolution(USER_EMAIL, "recConflict", "recExisting")

    assert tasks.resolved_conflict_pairs(USER_EMAIL) == {"recConflict": "recExisting"}


def test_resolving_twice_does_not_duplicate(registered_user):
    tasks.record_conflict_resolution(USER_EMAIL, "recConflict", "recExisting")
    tasks.record_conflict_resolution(USER_EMAIL, "recConflict", "recExisting")

    assert tasks.resolved_conflict_pairs(USER_EMAIL) == {"recConflict": "recExisting"}


def test_a_resolved_clash_is_cleared_and_says_so():
    session = _clashing()

    tasks.clear_resolved_conflicts([session], {"recConflict": "recExisting"})

    assert session.is_conflict is False
    assert session.conflict_type is None
    assert session.conflict_resolved is True


def test_a_clash_with_a_different_session_is_still_held():
    """Settling this pair says nothing about an overlap with some third session."""
    session = _clashing(other_id="recSomethingElse")

    tasks.clear_resolved_conflicts([session], {"recConflict": "recExisting"})

    assert session.conflict_type == "time"


def test_a_last_minute_hold_is_not_something_zoom_fixes():
    session = _clashing()
    session.conflict_type = "last_minute"

    tasks.clear_resolved_conflicts([session], {"recConflict": "recExisting"})

    assert session.conflict_type == "last_minute"


def test_a_resolved_session_is_booked_by_the_next_scan(wired):
    """The whole point: it must not be held back again in an hour."""
    tasks.scan_user(USER_EMAIL)
    assert wired["booked_ids"] == [["recClean1", "recClean2"]]

    tasks.record_conflict_resolution(USER_EMAIL, "recConflict", "recExisting")
    tasks.scan_user(USER_EMAIL)

    assert wired["booked_ids"][-1] == ["recClean1", "recClean2", "recConflict"]


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _note_client(monkeypatch, existing_note):
    """An Airtable client whose reads and writes are recorded rather than sent."""
    import airtable_integration

    written = {}
    reads = []

    def fake_get(url, **kwargs):
        reads.append(kwargs)
        return FakeResponse({"fields": {tasks.HOST_NOTES_FIELD: existing_note}})

    monkeypatch.setattr(airtable_integration.requests, "get", fake_get)
    monkeypatch.setattr(airtable_integration.requests, "patch",
                        lambda url, **kwargs: written.update(kwargs["json"]["fields"])
                        or FakeResponse({}))
    return airtable_integration.AirtableIntegration("patFakeKey"), written, reads


def test_the_host_note_is_added_to_the_session(monkeypatch):
    client, written, reads = _note_client(monkeypatch, "")

    client.append_session_note("recConflict", tasks.HOST_NOTES_FIELD,
                               tasks.ZOOM_RESOLUTION_NOTE)

    assert written[tasks.HOST_NOTES_FIELD] == tasks.ZOOM_RESOLUTION_NOTE


def test_a_note_someone_wrote_by_hand_is_kept(monkeypatch):
    """Host notes carry things the host needs — who is late, where the mic is."""
    client, written, reads = _note_client(monkeypatch, "They will be a few minutes late.")

    client.append_session_note("recConflict", tasks.HOST_NOTES_FIELD,
                               tasks.ZOOM_RESOLUTION_NOTE)

    assert written[tasks.HOST_NOTES_FIELD] == (
        f"They will be a few minutes late.\n{tasks.ZOOM_RESOLUTION_NOTE}")


def test_the_record_is_read_without_a_fields_parameter(monkeypatch):
    """Airtable's retrieve-a-record endpoint takes no "fields" — it 422s if sent one.

    Only list-records accepts it, and the first live click found that out.
    """
    client, _, reads = _note_client(monkeypatch, "")

    client.append_session_note("recConflict", tasks.HOST_NOTES_FIELD,
                               tasks.ZOOM_RESOLUTION_NOTE)

    assert reads and "params" not in reads[0]


def test_resolving_twice_does_not_write_the_note_twice(monkeypatch):
    client, written, reads = _note_client(monkeypatch, tasks.ZOOM_RESOLUTION_NOTE)

    client.append_session_note("recConflict", tasks.HOST_NOTES_FIELD,
                               tasks.ZOOM_RESOLUTION_NOTE)

    assert written == {}
