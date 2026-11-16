"""A clash between two booked sessions is settled by the scan, not left for a person.

The answer is almost always the same: the class booked second joins by Zoom and the
other keeps the Cisco machine. So the scan writes to that teacher itself, leaves the
host a note, records the clash as settled and books the session like any other.
"""
from datetime import date, timedelta

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
        # Completed sessions this school year, by teacher email. Absent means none.
        self.completed = {}
        self.count_calls = []

    def get_booked_sessions(self, **kwargs):
        return self._candidates()

    def get_all_sessions_for_schools(self, *args, **kwargs):
        return []

    def append_session_note(self, session_id, field, note):
        self.notes.append((session_id, field, note))

    def count_completed_sessions(self, email, since, limit):
        self.count_calls.append((email, since, limit))
        count = self.completed.get(email, 0)
        if isinstance(count, Exception):
            raise count
        return min(count, limit)


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


# A teacher with five or more completed sessions since August 1 knows the drill, so
# the overlap is not raised with anyone: no email, no note, no hold.

def test_an_experienced_teacher_is_not_emailed_and_both_sessions_are_booked(wired):
    wired["airtable"].completed["helycolina@example.com"] = 5

    tasks._scan_user(USER_EMAIL)

    assert wired["teacher"] == []
    assert wired["report"] == []
    assert wired["conflicts"] == []
    assert wired["airtable"].notes == []
    assert wired["booked"] == [["recFirst", "recSecond"]]
    # Nothing recorded as resolved: it was never a conflict to resolve.
    assert tasks.resolved_conflict_pairs(USER_EMAIL) == {}


def test_four_completed_sessions_still_gets_the_email(wired):
    wired["airtable"].completed["helycolina@example.com"] = 4

    tasks._scan_user(USER_EMAIL)

    assert len(wired["teacher"]) == 1
    assert wired["teacher"][0]["to"] == "helycolina@example.com"


def test_only_the_moving_teacher_is_counted(wired):
    # The teacher keeping the Cisco machine is not the one who would be emailed.
    wired["airtable"].completed["erikakozole@example.com"] = 20

    tasks._scan_user(USER_EMAIL)

    assert len(wired["teacher"]) == 1
    assert {email for email, _, _ in wired["airtable"].count_calls} == {"helycolina@example.com"}


def test_the_count_starts_on_august_first_of_this_school_year(wired):
    tasks._scan_user(USER_EMAIL)

    assert wired["airtable"].count_calls
    _, since, limit = wired["airtable"].count_calls[0]
    assert since == tasks.school_year_start()
    assert limit == tasks.EXPERIENCED_TEACHER_SESSIONS == 5


def test_if_airtable_cannot_count_the_email_still_goes(wired):
    wired["airtable"].completed["helycolina@example.com"] = RuntimeError("airtable down")

    tasks._scan_user(USER_EMAIL)

    assert len(wired["teacher"]) == 1


def test_an_experienced_teacher_is_not_flagged_even_with_auto_send_off(wired, monkeypatch):
    monkeypatch.setenv("AUTO_SEND_CONFLICT_EMAILS", "0")
    wired["airtable"].completed["helycolina@example.com"] = 7

    tasks._scan_user(USER_EMAIL)

    assert wired["teacher"] == []
    assert wired["conflicts"] == []
    assert wired["booked"] == [["recFirst", "recSecond"]]


def _co_taught_clash():
    first, second = _clash()
    second.teachers = ["Hely Colina", "Sam Arnaq"]
    second.teacher_emails = ["helycolina@example.com", "samarnaq@example.com"]
    return tasks.check_for_time_conflicts([first, second], [], now=BASE_TIME)


def test_co_teachers_are_not_waived_unless_all_of_them_qualify(wired):
    wired["airtable"].completed["helycolina@example.com"] = 9

    sessions = tasks.waive_experienced_teacher_conflicts(wired["airtable"], _co_taught_clash())

    assert all(s.is_conflict for s in sessions)


def test_co_teachers_are_waived_when_all_of_them_qualify(wired):
    wired["airtable"].completed["helycolina@example.com"] = 9
    wired["airtable"].completed["samarnaq@example.com"] = 5

    sessions = tasks.waive_experienced_teacher_conflicts(wired["airtable"], _co_taught_clash())

    assert not any(s.is_conflict for s in sessions)
    assert all(s.conflict_other_id is None for s in sessions)


@pytest.mark.parametrize("today, expected", [
    (date(2026, 11, 16), date(2026, 8, 1)),
    (date(2027, 3, 2), date(2026, 8, 1)),
    (date(2026, 8, 1), date(2026, 8, 1)),
    (date(2026, 7, 31), date(2025, 8, 1)),
])
def test_school_year_start(today, expected):
    assert tasks.school_year_start(today) == expected


def test_the_airtable_query_counts_this_teachers_completed_sessions():
    from airtable_integration import create_airtable_client

    client = create_airtable_client("k")
    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"records": [{"id": f"rec{i}"} for i in range(5)]}

    def fake_get(url, headers=None, params=None, timeout=None):
        sent.update(params)
        return Response()
    client.http.get = fake_get

    assert client.count_completed_sessions(" Hely.Colina@Example.com ", date(2026, 8, 1), 5) == 5
    formula = sent["filterByFormula"]
    assert "{Status} = 'Completed'" in formula
    assert "'2026-08-01'" in formula
    assert "',hely.colina@example.com,'" in formula
    assert "'Test'" in formula and "'PD Week'" in formula
    assert sent["maxRecords"] == 5
