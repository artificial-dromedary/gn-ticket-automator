"""Pressing "Book now" promises an email when the run finishes. It has to arrive.

A scheduled run stays quiet — it reports through the 5pm daily summary instead.
"""
from datetime import datetime, timedelta, timezone

import pytest

import tasks
from user_profiles import user_manager

from test_tasks import FakeAirtable, FakeSession, BASE_TIME, USER_EMAIL


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
    """Record every summary email instead of sending one."""
    airtable = FakeAirtable()
    sent = []

    monkeypatch.setattr(tasks, "create_airtable_client", lambda key: airtable)
    monkeypatch.setattr(tasks, "send_conflict_email", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "send_booking_summary_email",
                        lambda to, ok, bad, conflict_sessions=None, manual=False, **k: sent.append({
                            "to": to, "ok": list(ok), "failed": list(bad),
                            "conflicts": list(conflict_sessions or []), "manual": manual,
                        }))
    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", lambda send_to_gn, *a, **k: {
        "successful_sessions": [
            {"session_id": s.s_id, "title": s.title, "school": s.school,
             "teacher": s.teacher, "start_time": s.start_time.isoformat(),
             "length": s.length, "ticket_id": f"TKT-{s.s_id}"}
            for s in send_to_gn
        ],
        "failed_sessions": [],
    })
    return {"airtable": airtable, "sent": sent}


def test_manual_run_emails_the_person_who_asked(wired):
    tasks.book_sessions(USER_EMAIL, ["recClean1", "recClean2"], manual=True)

    assert len(wired["sent"]) == 1
    mail = wired["sent"][0]
    assert mail["to"] == USER_EMAIL
    assert mail["manual"] is True
    assert sorted(entry["ticket_id"] for entry in mail["ok"]) == ["TKT-recClean1", "TKT-recClean2"]


def test_scheduled_run_stays_quiet(wired):
    tasks.book_sessions(USER_EMAIL, ["recClean1", "recClean2"])
    assert wired["sent"] == []


def test_manual_email_lists_conflicts_that_were_skipped(wired):
    # recConflict overlaps an already-ticketed booking, so it is never sent to GN.
    tasks.book_sessions(USER_EMAIL, ["recClean1", "recClean2", "recConflict"], manual=True)

    mail = wired["sent"][0]
    assert [c["session_id"] for c in mail["conflicts"]] == ["recConflict"]
    assert "recConflict" not in [entry["session_id"] for entry in mail["ok"]]


def test_manual_email_still_arrives_when_there_is_nothing_to_book(wired):
    wired["airtable"]._candidates = lambda: []

    tasks.book_sessions(USER_EMAIL, ["recGone"], manual=True)

    assert len(wired["sent"]) == 1
    assert wired["sent"][0]["ok"] == []
    assert wired["sent"][0]["manual"] is True


def test_manual_email_reports_a_crash(monkeypatch, wired):
    def explode(*a, **k):
        raise RuntimeError("ServiceNow never loaded")

    monkeypatch.setattr(tasks.gn_ticket, "gn_ticket_handler", explode)

    with pytest.raises(RuntimeError):
        tasks.book_sessions(USER_EMAIL, ["recClean1"], manual=True)

    assert len(wired["sent"]) == 1
    assert "ServiceNow never loaded" in wired["sent"][0]["failed"][0]["error"]


def test_a_pending_request_makes_the_scan_book_manually(monkeypatch, wired):
    """The dashboard records a request, so the run it triggers must count as manual."""
    booked = []
    monkeypatch.setattr(tasks, "dispatch_booking",
                        lambda email, ids, manual=False: booked.append(manual))

    tasks.request_scan(USER_EMAIL)
    tasks.scan_user(USER_EMAIL)

    assert booked == [True]


def test_a_scan_nobody_asked_for_books_quietly(monkeypatch, wired):
    booked = []
    monkeypatch.setattr(tasks, "dispatch_booking",
                        lambda email, ids, manual=False: booked.append(manual))

    tasks.scan_user(USER_EMAIL)

    assert booked == [False]


def test_manual_scan_with_only_conflicts_still_emails(monkeypatch, wired):
    """Nothing to book, but the button still promised a reply."""
    wired["airtable"]._candidates = lambda: [
        FakeSession("recConflict", "Conflicted", "Qiqirtaq School",
                    BASE_TIME + timedelta(days=2), teacher="Third Teacher"),
    ]

    tasks.request_scan(USER_EMAIL)
    tasks.scan_user(USER_EMAIL)

    assert len(wired["sent"]) == 1
    mail = wired["sent"][0]
    assert mail["manual"] is True
    assert mail["ok"] == []
    assert [c["session_id"] for c in mail["conflicts"]] == ["recConflict"]
