"""A clash between two booked sessions is settled by the scan, not left for a person.

The answer is almost always the same: the class booked second joins by Zoom and the
other keeps the Cisco machine. So the scan writes to that teacher itself, leaves the
host a note, records the clash as settled and books the session like any other.
"""
from datetime import timedelta

import pytest

import tasks
from user_profiles import user_manager

from test_tasks import BASE_TIME, FakeSession, USER_EMAIL


def _clash():
    first = FakeSession("recFirst", "Shine Bright Like a Star!", "Jimmy Hikok Ilihakvik",
                        BASE_TIME + timedelta(days=3), teacher="Erika Kozole")
    second = FakeSession("recSecond", "On The Land Sled Dogs", "Jimmy Hikok Ilihakvik",
                         BASE_TIME + timedelta(days=3, minutes=20), teacher="Hely Colina")
    first.created_at = "2026-05-01T10:00:00.000Z"
    second.created_at = "2026-05-02T10:00:00.000Z"
    for s in (first, second):
        s.timezone = "America/Edmonton"
    return [first, second]


class Airtable:
    def __init__(self, candidates):
        self._candidates = candidates
        self.notes = []

    def get_booked_sessions(self, **kwargs):
        return self._candidates()

    def get_all_sessions_for_schools(self, *args, **kwargs):
        return []

    def append_session_note(self, session_id, field, note):
        self.notes.append((session_id, field, note))


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setenv("AUTO_SEND_CONFLICT_EMAILS", "1")
    user_manager.upsert_user(USER_EMAIL, name="Lead")
    user_manager.save_profile(USER_EMAIL, {
        "airtable_api_key": "patFakeKey",
        "servicenow_password": "hunter2",
        "totp_secret": "JBSWY3DPEHPK3PXP",
        "preferences": {"auto_booking_enabled": True},
    })
    airtable = Airtable(_clash)
    calls = {"airtable": airtable, "teacher": [], "report": [], "conflicts": [], "booked": []}
    monkeypatch.setattr(tasks, "create_airtable_client", lambda key: airtable)
    monkeypatch.setattr(tasks, "send_zoom_notice", lambda n: calls["teacher"].append(n))
    monkeypatch.setattr(tasks, "send_zoom_notices_sent_email",
                        lambda to, notices: calls["report"].append(list(notices)))
    monkeypatch.setattr(tasks, "send_conflict_email",
                        lambda to, sessions: calls["conflicts"].append(list(sessions)))
    monkeypatch.setattr(tasks, "send_booking_summary_email", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "dispatch_booking",
                        lambda email, ids, manual=False: calls["booked"].append(sorted(ids)))
    return calls


def test_the_moving_teacher_is_emailed_and_both_sessions_are_booked(wired):
    tasks._scan_user(USER_EMAIL)

    assert len(wired["teacher"]) == 1
    notice = wired["teacher"][0]
    assert notice["to"] == "helycolina@example.com"
    assert "your On The Land Sled Dogs session" in notice["body"]
    assert wired["airtable"].notes == [
        ("recSecond", tasks.HOST_NOTES_FIELD, tasks.ZOOM_RESOLUTION_NOTE)]
    assert wired["booked"] == [["recFirst", "recSecond"]]
    assert wired["conflicts"] == []
    assert len(wired["report"]) == 1


def test_a_settled_clash_is_not_emailed_again(wired):
    tasks._scan_user(USER_EMAIL)
    tasks._scan_user(USER_EMAIL)

    assert len(wired["teacher"]) == 1
    assert len(wired["report"]) == 1


def test_a_failed_send_leaves_the_session_flagged(wired, monkeypatch):
    def boom(notice):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(tasks, "send_zoom_notice", boom)

    tasks._scan_user(USER_EMAIL)

    assert wired["airtable"].notes == []
    assert wired["booked"] == []
    assert len(wired["conflicts"]) == 1
    assert tasks.resolved_conflict_pairs(USER_EMAIL) == {}


def test_switched_off_it_flags_as_before(wired, monkeypatch):
    monkeypatch.setenv("AUTO_SEND_CONFLICT_EMAILS", "0")

    tasks._scan_user(USER_EMAIL)

    assert wired["teacher"] == []
    assert len(wired["conflicts"]) == 1
