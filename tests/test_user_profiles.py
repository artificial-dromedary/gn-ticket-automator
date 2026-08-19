from user_profiles import user_manager


EMAIL = 'user@example.com'


def _save(**preferences):
    user_manager.upsert_user(EMAIL, name='Test User')
    user_manager.save_profile(EMAIL, {
        'airtable_api_key': 'air',
        'servicenow_password': 'snow',
        'totp_secret': 'totp',
        'preferences': preferences,
    })


def test_save_and_load_profile():
    _save(buffer_before=5, buffer_after=15)

    loaded = user_manager.load_profile(EMAIL)
    assert loaded['airtable_api_key'] == 'air'
    assert loaded['servicenow_password'] == 'snow'
    assert loaded['totp_secret'] == 'totp'
    assert loaded['preferences']['buffer_before'] == 5
    assert loaded['preferences']['buffer_after'] == 15
    assert loaded['preferences']['auto_booking_enabled'] is False


def test_credentials_are_encrypted_at_rest():
    _save()

    from sqlalchemy import select

    from db import SessionLocal
    from models import UserCredential

    with SessionLocal() as db:
        stored = db.execute(select(UserCredential)).scalars().one()

    assert stored.servicenow_password_enc != 'snow'
    assert 'snow' not in stored.servicenow_password_enc


def test_profile_is_incomplete_without_every_credential():
    user_manager.upsert_user(EMAIL, name='Test User')
    user_manager.save_profile(EMAIL, {
        'airtable_api_key': 'air',
        'servicenow_password': None,
        'totp_secret': None,
        'preferences': {},
    })

    assert user_manager.is_profile_complete(EMAIL) is False

    _save()
    assert user_manager.is_profile_complete(EMAIL) is True


def test_list_auto_enabled_users_returns_emails():
    _save(auto_booking_enabled=True)
    user_manager.upsert_user('off@example.com', name='Opted Out')
    user_manager.save_profile('off@example.com', {
        'airtable_api_key': 'air',
        'servicenow_password': 'snow',
        'totp_secret': 'totp',
        'preferences': {'auto_booking_enabled': False},
    })

    assert user_manager.list_auto_enabled_users() == [EMAIL]


def test_update_preferences_leaves_credentials_alone():
    _save(auto_booking_enabled=False)

    user_manager.update_preferences(EMAIL, {'auto_booking_enabled': True, 'buffer_before': 20})

    loaded = user_manager.load_profile(EMAIL)
    assert loaded['preferences']['auto_booking_enabled'] is True
    assert loaded['preferences']['buffer_before'] == 20
    assert loaded['airtable_api_key'] == 'air'
