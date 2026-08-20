"""The look-ahead slider: fixed stops, and a preference that survives a settings save."""
import pytest

from user_profiles import (DEFAULT_LOOKAHEAD_DAYS, LOOKAHEAD_DAYS, LOOKAHEAD_FOREVER_DAYS,
                           LOOKAHEAD_STOPS, normalize_lookahead, user_manager)


USER_EMAIL = "lead@takingitglobal.org"


def test_every_stop_normalizes_to_itself():
    for days in LOOKAHEAD_DAYS:
        assert normalize_lookahead(days) == days


def test_today_is_zero_and_survives_normalization():
    # 0 is falsy, which is exactly how "Today" used to get lost.
    assert LOOKAHEAD_STOPS[0][0] == 0
    assert normalize_lookahead(0) == 0
    assert normalize_lookahead("0") == 0


def test_labels_match_the_requested_scale():
    assert [label for _, label in LOOKAHEAD_STOPS] == [
        "Today", "7 days", "10 days", "14 days", "30 days", "60 days", "90 days", "Forever",
    ]


@pytest.mark.parametrize("value,expected", [
    (45, 60),        # between stops: round up so nothing drops out of view
    (1, 7),
    (91, LOOKAHEAD_FOREVER_DAYS),
    (99999, LOOKAHEAD_FOREVER_DAYS),
])
def test_values_between_stops_round_up(value, expected):
    assert normalize_lookahead(value) == expected


@pytest.mark.parametrize("value", [None, "", "abc", object()])
def test_junk_falls_back_to_the_default(value):
    assert normalize_lookahead(value) == DEFAULT_LOOKAHEAD_DAYS


def _register():
    user_manager.upsert_user(USER_EMAIL, name="Lead")
    user_manager.save_profile(USER_EMAIL, {
        "airtable_api_key": "patFakeKey",
        "servicenow_password": "hunter2",
        "totp_secret": "JBSWY3DPEHPK3PXP",
        "preferences": {"auto_booking_enabled": True},
    })


def test_today_persists_as_a_preference():
    _register()
    user_manager.update_preferences(USER_EMAIL, {"window_future_days": 0})
    assert user_manager.get_preferences(USER_EMAIL)["window_future_days"] == 0


def test_saving_other_settings_leaves_the_lookahead_alone():
    """The slider is not in the settings form, so a save must not reset it."""
    _register()
    user_manager.update_preferences(USER_EMAIL, {"window_future_days": 7})
    user_manager.update_preferences(USER_EMAIL, {"buffer_before": 15, "buffer_after": 15})
    assert user_manager.get_preferences(USER_EMAIL)["window_future_days"] == 7


def test_forever_round_trips():
    _register()
    user_manager.update_preferences(USER_EMAIL, {"window_future_days": LOOKAHEAD_FOREVER_DAYS})
    assert user_manager.load_profile(USER_EMAIL)["preferences"]["window_future_days"] == (
        LOOKAHEAD_FOREVER_DAYS)
