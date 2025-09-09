from user_profiles import UserProfileManager

def test_save_and_load_profile(tmp_path):
    db_file = tmp_path / 'profiles.db'
    manager = UserProfileManager(db_file)
    profile_data = {
        'airtable_api_key': 'air',
        'servicenow_password': 'snow',
        'totp_secret': 'totp',
        'preferences': {'theme': 'dark'}
    }
    manager.save_profile('user@example.com', profile_data)
    loaded = manager.load_profile('user@example.com')
    assert loaded['airtable_api_key'] == 'air'
    assert loaded['servicenow_password'] == 'snow'
    assert loaded['preferences']['theme'] == 'dark'
    assert 'chatgpt_api_key' not in loaded

