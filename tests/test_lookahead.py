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


# --- Saving the slider ------------------------------------------------------
# Narrowing the window is filtered in the browser, so nothing reaches the server
# unless the page asks it to. Without that the choice was lost on the next load —
# and the scheduled run, which reads the same preference, never narrowed at all.


@pytest.fixture
def client():
    import main

    main.app.config["TESTING"] = True
    main.app.secret_key = "test-secret"
    test_client = main.app.test_client()
    with test_client.session_transaction() as flask_session:
        flask_session["user"] = {"email": USER_EMAIL, "name": "Lead"}
    return test_client


def test_narrowing_the_slider_is_saved(client):
    _register()

    response = client.post("/gn_ticket/set_lookahead", json={"window_future_days": 7})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "window_future_days": 7, "label": "7 days"}
    assert user_manager.get_preferences(USER_EMAIL)["window_future_days"] == 7


def test_today_is_saved_rather_than_read_as_missing(client):
    _register()

    response = client.post("/gn_ticket/set_lookahead", json={"window_future_days": 0})

    assert response.get_json()["label"] == "Today"
    assert user_manager.get_preferences(USER_EMAIL)["window_future_days"] == 0


def test_a_value_off_the_scale_snaps_to_a_stop(client):
    _register()

    response = client.post("/gn_ticket/set_lookahead", json={"window_future_days": 45})

    assert response.get_json()["window_future_days"] == 60
    assert user_manager.get_preferences(USER_EMAIL)["window_future_days"] == 60


def test_a_request_without_the_field_changes_nothing(client):
    _register()
    user_manager.update_preferences(USER_EMAIL, {"window_future_days": 14})

    response = client.post("/gn_ticket/set_lookahead", json={})

    assert response.status_code == 400
    assert user_manager.get_preferences(USER_EMAIL)["window_future_days"] == 14


# --- The scheduled run reads the same window --------------------------------


class WindowRecordingAirtable:
    """Remembers the horizon each query was given, and returns nothing."""

    def __init__(self):
        self.windows = []

    def get_booked_sessions(self, user_email=None, window_past_days=14, window_future_days=90):
        self.windows.append(window_future_days)
        return []

    def get_all_sessions_for_schools(self, school_names, status_filters=None,
                                     window_past_days=14, window_future_days=90):
        self.windows.append(window_future_days)
        return []


def test_the_scheduled_scan_uses_the_saved_lookahead(monkeypatch):
    import tasks

    _register()
    user_manager.update_preferences(USER_EMAIL, {"window_future_days": 7})

    airtable = WindowRecordingAirtable()
    monkeypatch.setattr(tasks, "create_airtable_client", lambda key: airtable)

    tasks.scan_user(USER_EMAIL)

    assert airtable.windows == [7]


def test_the_booking_pass_uses_the_saved_lookahead(monkeypatch):
    """The re-read before booking has to honour a window narrowed since the scan."""
    import tasks

    _register()
    user_manager.update_preferences(USER_EMAIL, {"window_future_days": 0})

    airtable = WindowRecordingAirtable()
    monkeypatch.setattr(tasks, "create_airtable_client", lambda key: airtable)
    monkeypatch.setattr(tasks, "DRY_RUN", True)

    tasks.book_sessions(USER_EMAIL, ["recSomething"])

    assert airtable.windows == [0]
