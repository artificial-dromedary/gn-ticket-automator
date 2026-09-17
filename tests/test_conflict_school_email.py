"""A clash between two sessions ends with an email to a teacher, ready to paste.

The notification says what was held back; the draft under it goes to the one
teacher whose class would move to Zoom. The other class keeps the Cisco machine
and is not asked for anything, so it is not written to and not named.
"""
from datetime import datetime, timedelta, timezone

import emailer
from conflict import check_for_time_conflicts
from emailer import (teacher_conflict_email, teacher_conflict_recipients,
                     teacher_conflict_subject)

from test_tasks import FakeSession

JUNE_4_10AM_EDT = datetime(2026, 6, 4, 14, 0, tzinfo=timezone.utc)


def _pair(first_created, second_created, ticketed=None):
    fish = FakeSession("recFish", "Beam Paints Watercolour - Fish", "Nakasuk School",
                       JUNE_4_10AM_EDT, teacher="Frederick Addae")
    beads = FakeSession("recBeads", "Blueberry Beading", "Nakasuk School",
                        JUNE_4_10AM_EDT + timedelta(minutes=30), teacher="Nicole King")
    for session, created in ((fish, first_created), (beads, second_created)):
        session.created_at = created
        session.timezone = "America/New_York"
    if ticketed is not None:
        ticketed.gn_ticket_requested = True
    return fish, beads


def _now():
    return JUNE_4_10AM_EDT - timedelta(days=10)


def test_the_draft_matches_the_wording_sent_to_teachers():
    fish, beads = _pair("2026-05-01T10:00:00.000Z", "2026-05-02T10:00:00.000Z")
    check_for_time_conflicts([fish, beads], [], now=_now())

    assert teacher_conflict_email(beads) == (
        "Hi there,\n\n"
        "I just wanted to let you know that there are two sessions overlapping for "
        "your school. In most cases, as long as your internet is pretty reliable, you "
        "can connect from the classroom via Zoom (instead of the Cisco videoconference "
        "machine). If you've had trouble with Zoom and video streaming in the past, "
        "let me know and we can rebook your session."
    )


def test_only_the_teacher_who_would_move_to_zoom_is_written_to():
    """The other class keeps the machine, so there is nothing to ask its teacher."""
    fish, beads = _pair("2026-05-01T10:00:00.000Z", "2026-05-02T10:00:00.000Z")
    check_for_time_conflicts([fish, beads], [], now=_now())

    # Beads was booked second, so it is the one that would move.
    assert teacher_conflict_recipients(beads) == "nicoleking@example.com"
    # Read from the other side of the same clash, the recipient does not change.
    assert teacher_conflict_recipients(fish) == "nicoleking@example.com"


def test_every_teacher_on_the_moving_session_is_written_to():
    fish, beads = _pair("2026-05-01T10:00:00.000Z", "2026-05-02T10:00:00.000Z")
    beads.teachers = ["Nicole King", "Arlene Vasquez"]
    beads.teacher_emails = ["nicoleking@example.com", "avasquez@example.com"]
    check_for_time_conflicts([fish, beads], [], now=_now())

    assert teacher_conflict_recipients(beads) == ("nicoleking@example.com, "
                                                  "avasquez@example.com")


def test_the_subject_carries_the_date_in_the_school_zone():
    fish, beads = _pair("2026-05-01T10:00:00.000Z", "2026-05-02T10:00:00.000Z")
    check_for_time_conflicts([fish, beads], [], now=_now())

    assert teacher_conflict_subject(beads) == (
        "FYI - Connect via Zoom for June 4 Connected North session")


def test_the_subject_says_sept_not_sep():
    """The date reads the way a person writes it, not the way strftime does."""
    assert emailer.short_date("2026-09-24T17:00:00+00:00", "Canada/Eastern") == "Sept 24"


def test_the_session_booked_first_keeps_the_cisco_machine_from_either_side():
    fish, beads = _pair("2026-05-03T10:00:00.000Z", "2026-05-02T10:00:00.000Z")
    check_for_time_conflicts([fish, beads], [], now=_now())

    # Beads was booked first this time, so Frederick is the one asked to move.
    for session in (fish, beads):
        assert teacher_conflict_recipients(session) == "frederickaddae@example.com"


def test_an_already_ticketed_session_keeps_the_machine_even_if_created_later():
    fish, beads = _pair("2026-05-01T10:00:00.000Z", "2026-05-09T10:00:00.000Z")
    beads.gn_ticket_requested = True
    check_for_time_conflicts([fish], [beads], now=_now())

    assert teacher_conflict_recipients(fish) == "frederickaddae@example.com"


def test_no_draft_for_holds_that_are_not_a_clash_between_two_sessions():
    assert teacher_conflict_email({"conflict_type": "last_minute"}) is None
    assert teacher_conflict_email({"conflict_type": "ghost_ticket"}) is None


def test_the_notification_carries_one_draft_per_clashing_pair(monkeypatch):
    import tasks

    fish, beads = _pair("2026-05-01T10:00:00.000Z", "2026-05-02T10:00:00.000Z")
    check_for_time_conflicts([fish, beads], [], now=_now())
    payload = [tasks._session_to_dict(s) for s in (fish, beads)]

    sent = []
    monkeypatch.setattr(emailer, "_send", lambda to, subject, body: sent.append(body))
    emailer.send_conflict_email("lead@example.com", payload)

    body = sent[0]
    assert body.index("These sessions were NOT booked") < body.index("DRAFT EMAIL TO TEACHERS")
    assert body.count("Hi there,") == 1
    assert "To: Nicole King <nicoleking@example.com>" in body
    assert "Subject: FYI - Connect via Zoom for June 4 Connected North session" in body


def test_the_daily_summary_reads_booked_then_conflicts_then_teachers_then_draft(monkeypatch):
    import tasks

    fish, beads = _pair("2026-05-01T10:00:00.000Z", "2026-05-02T10:00:00.000Z")
    check_for_time_conflicts([fish, beads], [], now=_now())
    payload = [tasks._session_to_dict(s) for s in (fish, beads)]
    booked = [{"title": "Clean One", "school": "Inuksuk High School",
               "start_time": JUNE_4_10AM_EDT.isoformat(), "ticket_id": "TKT-1"}]
    removed = [{"title": "Taken Off", "school": "Nakasuk School",
                "start_time": JUNE_4_10AM_EDT.isoformat()}]

    sent = []
    monkeypatch.setattr(emailer, "_send", lambda to, subject, body: sent.append(body))
    emailer.send_daily_summary_email("lead@example.com", booked, payload, "2026-06-01",
                                     excluded_sessions=removed)

    body = sent[0]
    order = [body.index("BOOKED TODAY"), body.index("CONFLICTS NEEDING YOU"),
             body.index("DRAFT EMAIL TO TEACHERS"),
             body.index("To: Nicole King <nicoleking@example.com>"),
             body.index("Hi there,"), body.index("REMOVED, NOT BEING BOOKED")]
    assert order == sorted(order)
    assert "frederickaddae@example.com" not in body

