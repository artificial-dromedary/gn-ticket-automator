"""Automated scan/book behaviour: clean sessions get booked even when others conflict."""
from datetime import datetime, timedelta, timezone

import threading
import time

import pytest

import tasks
from models import ConflictEmailLog, ScanResult, TaskLock, User
from db import SessionLocal
from sqlalchemy import select
from ticket_submission_log import TicketSubmissionLog
from user_profiles import user_manager


USER_EMAIL = "lead@takingitglobal.org"
# Relative to now, not a fixed date: candidates have to stay comfortably outside the
# last-minute window, and a hardcoded date silently walks into it as time passes.
BASE_TIME = (datetime.now(timezone.utc) + timedelta(days=30)).replace(
    hour=15, minute=0, second=0, microsecond=0)


class FakeSession:
    def __init__(self, s_id, title, school, start_time, length=60,
                 status="Booked", teacher="Teacher", gn_ticket_requested=False):
        self.s_id = s_id
        self.title = title
        self.school = school
        self.start_time = start_time
        self.length = length
        self.status = status
        self.teacher = teacher
        self.teacher_email = f"{teacher.lower().replace(' ', '')}@example.com"
        self.gn_ticket_requested = gn_ticket_requested
        self.created_at = ""
        self.is_conflict = False
        self.conflict_details = ""
        self.conflict_type = None
        self.conflict_start_iso = None
        self.conflict_end_iso = None


def candidate_factory():
    """Two clean sessions plus one that overlaps an already-ticketed booking."""
    return [
        FakeSession("recClean1", "Clean One", "Nakasuk School", BASE_TIME),
        FakeSession("recClean2", "Clean Two", "Inuksuk High School",
                    BASE_TIME + timedelta(days=1), teacher="Other Teacher"),
        FakeSession("recConflict", "Conflicted", "Qiqirtaq School",
                    BASE_TIME + timedelta(days=2), teacher="Third Teacher"),
    ]


def existing_factory():
    return [
        FakeSession("recExisting", "Already Ticketed", "Qiqirtaq School",
                    BASE_TIME + timedelta(days=2), teacher="Someone Else",
                    gn_ticket_requested=True),
    ]


class FakeAirtable:
    """Returns fresh session objects each call — conflict detection mutates them."""

    def __init__(self, candidates=candidate_factory, existing=existing_factory):
        self._candidates = candidates
        self._existing = existing
        self.booked_calls = 0

    def get_booked_sessions(self, user_email=None, window_past_days=14, window_future_days=90):
        self.booked_calls += 1
        return self._candidates()

    def get_all_sessions_for_schools(self, school_names, status_filters=None,
                                     window_past_days=14, window_future_days=90):
        return self._existing()


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
    """Patch out Airtable, email and the booking queue; record what each was asked to do."""
    airtable = FakeAirtable()
    calls = {"booked_ids": [], "conflict_emails": [], "manual_flags": []}

    monkeypatch.setattr(tasks, "create_airtable_client", lambda key: airtable)
    monkeypatch.setattr(tasks, "send_conflict_email",
                        lambda email, sessions: calls["conflict_emails"].append(list(sessions)))
    monkeypatch.setattr(tasks.book_sessions, "delay",
                        lambda email, ids, manual=False: (
                            calls["booked_ids"].append(list(ids)),
                            calls["manual_flags"].append(manual)))
    calls["airtable"] = airtable
    return calls


def test_clean_sessions_are_booked_even_when_others_conflict(wired):
    tasks.scan_user(USER_EMAIL)

    assert wired["booked_ids"] == [["recClean1", "recClean2"]]

    assert len(wired["conflict_emails"]) == 1
    emailed = wired["conflict_emails"][0]
    assert [entry["session_id"] for entry in emailed] == ["recConflict"]


def test_scan_records_result_summary(wired):
    tasks.scan_user(USER_EMAIL)

    with SessionLocal() as db:
        scan = db.execute(select(ScanResult)).scalars().one()
    import json
    summary = json.loads(scan.summary)
    assert summary == {"candidates": 3, "conflicts": 1, "clean": 2}


def test_all_conflicts_books_nothing(monkeypatch, wired):
    def all_conflicted():
        return [FakeSession("recConflict", "Conflicted", "Qiqirtaq School",
                            BASE_TIME + timedelta(days=2))]

    wired["airtable"]._candidates = all_conflicted

    tasks.scan_user(USER_EMAIL)

    assert wired["booked_ids"] == []
    assert len(wired["conflict_emails"]) == 1


def test_conflict_is_emailed_only_once(wired):
    tasks.scan_user(USER_EMAIL)
    tasks.scan_user(USER_EMAIL)

    assert len(wired["conflict_emails"]) == 1

    with SessionLocal() as db:
        rows = db.execute(select(ConflictEmailLog)).scalars().all()
    assert [row.session_id for row in rows] == ["recConflict"]
    assert rows[0].conflict_session_id == "recExisting"


def test_conflict_emails_again_when_the_counterpart_changes(wired):
    tasks.scan_user(USER_EMAIL)

    def different_counterpart():
        return [FakeSession("recOther", "A Different Booking", "Qiqirtaq School",
                            BASE_TIME + timedelta(days=2), teacher="Someone Else",
                            gn_ticket_requested=True)]

    wired["airtable"]._existing = different_counterpart
    tasks.scan_user(USER_EMAIL)

    assert len(wired["conflict_emails"]) == 2


def test_booking_skips_sessions_that_became_conflicted(monkeypatch, wired):
    handled = {}

    def fake_handler(send_to_gn, *args, **kwargs):
        handled["ids"] = [s.s_id for s in send_to_gn]
        return {
            "successful_sessions": [
                {"session_id": s.s_id, "title": s.title, "school": s.school,
                 "teacher": s.teacher, "start_time": s.start_time.isoformat(),
                 "length": s.length, "ticket_id": f"TKT-{s.s_id}"}
                for s in send_to_gn
            ],
            "failed_sessions": [],
        }

    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", fake_handler)

    # recConflict was clean when the scan ran, but conflicts by the time booking starts.
    tasks.book_sessions(USER_EMAIL, ["recClean1", "recClean2", "recConflict"])

    assert handled["ids"] == ["recClean1", "recClean2"]

    # What the end-of-day summary is built from.
    logged = TicketSubmissionLog().get_entries(USER_EMAIL)
    assert sorted(entry["ticket_id"] for entry in logged) == ["TKT-recClean1", "TKT-recClean2"]


def test_booking_outcome_lands_on_the_latest_scan(monkeypatch, wired):
    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", lambda send_to_gn, *a, **k: {
        "successful_sessions": [
            {"session_id": s.s_id, "title": s.title, "school": s.school,
             "teacher": s.teacher, "start_time": s.start_time.isoformat(),
             "length": s.length, "ticket_id": "TKT-1"}
            for s in send_to_gn[:1]
        ],
        "failed_sessions": [{"title": "Clean Two", "error": "ServiceNow timeout"}],
    })

    tasks.scan_user(USER_EMAIL)
    tasks.book_sessions(USER_EMAIL, ["recClean1", "recClean2"])

    with SessionLocal() as db:
        scan = db.execute(select(ScanResult)).scalars().one()
    import json
    summary = json.loads(scan.summary)
    assert summary["booked"] == 1
    assert summary["failed"] == 1
    assert summary["booked_at"]


def test_incomplete_profile_is_skipped(monkeypatch, wired):
    user_manager.save_profile(USER_EMAIL, {
        "airtable_api_key": "patFakeKey",
        "servicenow_password": None,
        "totp_secret": None,
        "preferences": {"auto_booking_enabled": True},
    })

    tasks.scan_user(USER_EMAIL)

    assert wired["booked_ids"] == []
    assert wired["airtable"].booked_calls == 0


def test_run_scheduled_scan_only_enqueues_opted_in_users(monkeypatch, registered_user):
    user_manager.upsert_user("optedout@takingitglobal.org", name="Other")
    user_manager.save_profile("optedout@takingitglobal.org", {
        "airtable_api_key": "patOther",
        "servicenow_password": "pw",
        "totp_secret": "JBSWY3DPEHPK3PXP",
        "preferences": {"auto_booking_enabled": False},
    })

    enqueued = []
    monkeypatch.setattr(tasks.scan_user, "delay", lambda email: enqueued.append(email))

    tasks.run_scheduled_scan()

    assert enqueued == [USER_EMAIL]


def test_lock_stops_a_second_run_while_one_is_in_flight(wired):
    """The guard against two booking runs filing duplicate tickets for one session."""
    with tasks._user_lock("book:someone@example.org", 60) as first:
        with tasks._user_lock("book:someone@example.org", 60) as second:
            assert first is True
            assert second is False


def test_different_users_do_not_block_each_other(wired):
    with tasks._user_lock("book:one@example.org", 60) as first:
        with tasks._user_lock("book:two@example.org", 60) as second:
            assert first is True
            assert second is True


def test_lock_is_released_after_a_run(wired):
    with tasks._user_lock("book:someone@example.org", 60) as first:
        assert first is True
    with tasks._user_lock("book:someone@example.org", 60) as second:
        assert second is True

    with SessionLocal() as db:
        assert db.execute(select(TaskLock)).scalars().all() == []


def test_an_expired_lock_is_reclaimed(wired):
    """A process killed mid-booking must not block its user forever."""
    with tasks._user_lock("book:someone@example.org", 60):
        with SessionLocal() as db:
            lock = db.execute(select(TaskLock)).scalars().one()
            lock.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()

        with tasks._user_lock("book:someone@example.org", 60) as reclaimed:
            assert reclaimed is True


def test_a_locked_out_scan_does_no_work(monkeypatch, wired):
    """Being locked out must skip the run, not run it unprotected."""
    monkeypatch.setattr(tasks, "_scan_user",
                        lambda email: pytest.fail("locked-out scan must not run"))

    with tasks._user_lock(f"scan:{USER_EMAIL}", 60):
        tasks.scan_user(USER_EMAIL)


def test_the_lock_fails_closed_when_the_table_is_unreachable(monkeypatch, wired):
    """A lock that cannot be taken skips the work rather than running unguarded."""
    def unreachable(*args, **kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(tasks, "_try_acquire_lock", unreachable)

    with tasks._user_lock("book:someone@example.org", 60) as acquired:
        assert acquired is False

    tasks.scan_user(USER_EMAIL)
    assert wired["booked_ids"] == []


def test_only_one_booking_run_holds_the_slot(wired):
    """Chrome is the memory ceiling: two concurrent runs is how one gets killed."""
    with tasks.booking_slot(wait_seconds=0) as first:
        with tasks.booking_slot(wait_seconds=0) as second:
            assert first is True
            assert second is False


def test_a_busy_slot_is_waited_for_not_failed(monkeypatch, wired):
    """The waiter should queue and then get in, rather than give up."""
    monkeypatch.setattr(tasks, "BUSY_POLL_SECONDS", 0.01)
    notices = []

    holder = tasks.booking_slot(wait_seconds=0)
    assert holder.__enter__() is True

    def release_shortly():
        time.sleep(0.05)
        holder.__exit__(None, None, None)

    threading.Thread(target=release_shortly).start()

    with tasks.booking_slot(wait_seconds=5, on_wait=lambda s: notices.append(s)) as got:
        assert got is True, "waiter should have been let in once the slot freed"

    assert notices, "the waiter should have announced that it was waiting"


def test_a_waiter_gives_up_after_its_deadline(monkeypatch, wired):
    monkeypatch.setattr(tasks, "BUSY_POLL_SECONDS", 0.01)

    with tasks.booking_slot(wait_seconds=0):
        with tasks.booking_slot(wait_seconds=0.05) as second:
            assert second is False


def test_booking_in_progress_reports_the_slot(wired):
    assert tasks.booking_in_progress() is None
    with tasks.booking_slot(wait_seconds=0):
        assert tasks.booking_in_progress() is not None
    assert tasks.booking_in_progress() is None


def test_a_dead_holder_frees_the_slot_by_expiry(wired):
    """A run killed for memory must not wedge the nightly job until the TTL is long gone."""
    with tasks.booking_slot(wait_seconds=0):
        with SessionLocal() as db:
            lock = db.execute(select(TaskLock)).scalars().one()
            lock.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()

        with tasks.booking_slot(wait_seconds=0) as taken_over:
            assert taken_over is True


def test_a_heartbeat_keeps_a_live_holder_from_expiring(wired):
    """The other half of that: a holder still working must not lose its slot."""
    with tasks.booking_slot(wait_seconds=0):
        with SessionLocal() as db:
            lock = db.execute(select(TaskLock)).scalars().one()
            key, token = lock.name, lock.token
            lock.expires_at = datetime.utcnow() + timedelta(seconds=1)
            db.commit()

        assert tasks._renew_lock(key, token, 600) is True

        with SessionLocal() as db:
            refreshed = db.execute(select(TaskLock)).scalars().one()
        assert refreshed.expires_at > datetime.utcnow() + timedelta(seconds=300)


def test_renewing_a_lock_we_no_longer_hold_reports_failure(wired):
    assert tasks._renew_lock("gn:lock:nobody-holds-this", "sometoken", 60) is False


def test_a_booking_that_cannot_get_the_slot_books_nothing(monkeypatch, wired):
    """Blocked means postponed, not partially done."""
    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler",
                        lambda *a, **k: pytest.fail("must not drive Chrome without the slot"))
    monkeypatch.setattr(tasks, "BUSY_MAX_WAIT_SECONDS", 0)

    with tasks.booking_slot(wait_seconds=0):
        tasks.book_sessions(USER_EMAIL, ["recClean1", "recClean2"])

    # Nothing recorded: the sessions are untouched and go to the next run.
    assert TicketSubmissionLog().get_entries(USER_EMAIL) == []
