"""Where a user's mail goes.

Sign-in is tied to one organisation's addresses, and the booking notices do not
have to follow it there. Every send resolves its recipient through
user_manager.notification_email(), so the account address stays the identity a
scan runs under while the mailbox it reports to is a separate, editable choice.
"""
from datetime import datetime, timezone

import pytest

import tasks
from user_profiles import normalize_notification_email, user_manager

from test_tasks import USER_EMAIL, registered_user  # noqa: F401

ELSEWHERE = "somebody@example.com"


def set_notification_email(email, address):
    user_manager.update_preferences(email, {"notification_email": address})


@pytest.mark.parametrize("value,expected", [
    ("somebody@example.com", "somebody@example.com"),
    ("  Somebody@Example.COM  ", "somebody@example.com"),
    ("first.last@mail.example.co.uk", "first.last@mail.example.co.uk"),
    # Nothing usable: the account address is a better answer than a bounce.
    ("", None),
    ("   ", None),
    (None, None),
    ("not-an-address", None),
    ("@example.com", None),
    ("somebody@localhost", None),
    ("two words@example.com", None),
])
def test_only_a_usable_address_is_stored(value, expected):
    assert normalize_notification_email(value) == expected


def test_mail_goes_to_the_account_address_by_default(registered_user):
    assert user_manager.notification_email(USER_EMAIL) == USER_EMAIL


def test_a_chosen_address_takes_over(registered_user):
    set_notification_email(USER_EMAIL, ELSEWHERE)

    assert user_manager.notification_email(USER_EMAIL) == ELSEWHERE


def test_clearing_the_field_hands_mail_back_to_the_account(registered_user):
    set_notification_email(USER_EMAIL, ELSEWHERE)
    set_notification_email(USER_EMAIL, "")

    assert user_manager.notification_email(USER_EMAIL) == USER_EMAIL


def test_an_unusable_entry_does_not_send_mail_nowhere(registered_user):
    set_notification_email(USER_EMAIL, "not-an-address")

    assert user_manager.notification_email(USER_EMAIL) == USER_EMAIL


def test_an_unknown_user_is_their_own_address(registered_user):
    """Nothing to look up, and a send site still needs somewhere to aim."""
    assert user_manager.notification_email("stranger@example.com") == "stranger@example.com"


def test_the_address_survives_a_round_trip(registered_user):
    set_notification_email(USER_EMAIL, ELSEWHERE)

    assert user_manager.get_preferences(USER_EMAIL)["notification_email"] == ELSEWHERE
    assert user_manager.load_profile(USER_EMAIL)["preferences"]["notification_email"] == ELSEWHERE


def test_the_daily_summary_is_addressed_to_the_chosen_mailbox(monkeypatch, registered_user):
    set_notification_email(USER_EMAIL, ELSEWHERE)
    addressed = []
    monkeypatch.setattr(tasks, "send_daily_summary_email",
                        lambda to, *args, **kwargs: addressed.append(to))

    assert tasks.send_daily_summaries(force=True) == 1
    assert addressed == [ELSEWHERE]


def test_the_summary_is_still_recorded_against_the_account(monkeypatch, registered_user):
    """Only the envelope follows the preference. Everything the summary is keyed
    by — who has been sent today's, whose bookings it lists — stays the account."""
    set_notification_email(USER_EMAIL, ELSEWHERE)
    monkeypatch.setattr(tasks, "send_daily_summary_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_local_now",
                        lambda: datetime.now(timezone.utc).astimezone().replace(hour=18))

    assert tasks.send_daily_summaries() == 1
    # Recorded under the account, so the second run of the day sends nothing.
    assert tasks.send_daily_summaries() == 0


def test_conflict_notices_follow_the_preference(monkeypatch, registered_user):
    set_notification_email(USER_EMAIL, ELSEWHERE)
    addressed = []
    monkeypatch.setattr(tasks, "send_conflict_email",
                        lambda to, sessions: addressed.append(to))

    tasks._email_new_conflicts(USER_EMAIL, [
        {"session_id": "recA", "conflict_session_id": "recB", "title": "Session A"},
    ])

    assert addressed == [ELSEWHERE]


def test_booking_summaries_follow_the_preference(monkeypatch, registered_user):
    """The report on a booking someone started by hand, sent from the web service."""
    import main

    set_notification_email(USER_EMAIL, ELSEWHERE)
    addressed = []
    monkeypatch.setattr(main, "send_booking_summary_email",
                        lambda to, *args, **kwargs: addressed.append(to))

    main.notify_booking_finished(USER_EMAIL, [], [])

    assert addressed == [ELSEWHERE]
