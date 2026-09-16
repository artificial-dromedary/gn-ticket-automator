"""A clash between two sessions ends with an email to the school, ready to paste.

The notification says what was held back; the draft under it says it to the
teachers, in the school's own time zone, naming who keeps the Cisco machine.
"""
from datetime import datetime, timedelta, timezone

import emailer
from conflict import check_for_time_conflicts
from emailer import teacher_conflict_email

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


def test_the_draft_matches_the_wording_sent_to_schools():
    fish, beads = _pair("2026-05-01T10:00:00.000Z", "2026-05-02T10:00:00.000Z")
    check_for_time_conflicts([fish, beads], [], now=_now())

    assert teacher_conflict_email(beads) == (
        "Hi there,\n\n"
        "In setting up the videoconference connection, I saw that there are two sessions "
        "overlapping for your school:\n\n"
        "• Beam Paints Watercolour - Fish (Frederick Addae): Thursday, June 4 at 10:00 AM EDT\n\n"
        "• Blueberry Beading (Nicole King): Thursday, June 4 at 10:30 AM EDT\n\n"
        "If your internet is pretty reliable/fast, one teacher can connect from the classroom "
        "via Zoom (rather than the Cisco machine). Otherwise, Frederick's session will "
        "connect on the Cisco machine as it is already set up, and we can rebook Nicole's "
        "session.\n\n"
        "Could you let me know what you'd like to do?"
    )


def test_the_session_booked_first_keeps_the_cisco_machine_from_either_side():
    fish, beads = _pair("2026-05-03T10:00:00.000Z", "2026-05-02T10:00:00.000Z")
    check_for_time_conflicts([fish, beads], [], now=_now())

    for session in (fish, beads):
        draft = teacher_conflict_email(session)
        assert "Otherwise, Nicole's session will connect on the Cisco machine" in draft
        assert "we can rebook Frederick's session" in draft


def test_an_already_ticketed_session_keeps_the_machine_even_if_created_later():
    fish, beads = _pair("2026-05-01T10:00:00.000Z", "2026-05-09T10:00:00.000Z")
    beads.gn_ticket_requested = True
    check_for_time_conflicts([fish], [beads], now=_now())

    draft = teacher_conflict_email(fish)
    assert "Otherwise, Nicole's session will connect on the Cisco machine" in draft


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
    assert "Thursday, June 4 at 10:30 AM EDT" in body


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
             body.index("Frederick Addae: frederickaddae@example.com"),
             body.index("Nicole King: nicoleking@example.com"),
             body.index("Hi there,"), body.index("REMOVED, NOT BEING BOOKED")]
    assert order == sorted(order)

