"""Moving off the desktop app without redoing setup.

A desktop user's gn_ticket.db has the same tables as this service, encrypted with
the key built into the app. They upload it here and pick up where they left off.
"""
import io
import sqlite3

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

import main
from db import SessionLocal
from desktop_import import DesktopImportError, import_desktop_files, import_desktop_profile
from models import ConflictEmailLog, TicketSubmission, User
from user_profiles import user_manager

EMAIL = "lead@takingitglobal.org"
DESKTOP_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def desktop_key(monkeypatch):
    monkeypatch.setenv("DESKTOP_APP_ENCRYPTION_KEY", DESKTOP_KEY)


def make_desktop_db(path, email=EMAIL, key=DESKTOP_KEY, totp="JBSWY3DPEHPK3PXP"):
    """A database as an older desktop build left it: no scan_frequency_hours column."""
    fernet = Fernet(key.encode())
    enc = lambda value: fernet.encrypt(value.encode()).decode() if value else None
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255), name VARCHAR(255),
            picture_url VARCHAR(1024), created_at DATETIME, last_login_at DATETIME);
        CREATE TABLE user_credentials (id INTEGER PRIMARY KEY, user_id INTEGER,
            airtable_api_key_enc TEXT, servicenow_password_enc TEXT, totp_secret_enc TEXT, updated_at DATETIME);
        CREATE TABLE user_preferences (id INTEGER PRIMARY KEY, user_id INTEGER, buffer_before INTEGER,
            buffer_after INTEGER, auto_booking_enabled BOOLEAN, window_past_days INTEGER,
            window_future_days INTEGER, updated_at DATETIME);
        CREATE TABLE ticket_submissions (id INTEGER PRIMARY KEY, user_id INTEGER, submitted_at DATETIME,
            session_id VARCHAR(255), title VARCHAR(512), school VARCHAR(512), teacher VARCHAR(512),
            ticket_id VARCHAR(255), start_time DATETIME, length INTEGER, status VARCHAR(64));
        CREATE TABLE conflict_email_log (id INTEGER PRIMARY KEY, user_id INTEGER,
            session_id VARCHAR(255), conflict_session_id VARCHAR(255), emailed_at DATETIME);
    """)
    conn.execute("INSERT INTO users VALUES (1, ?, 'Lead', '', '2026-01-01 00:00:00', '2026-01-01 00:00:00')", (email,))
    conn.execute("INSERT INTO user_credentials VALUES (1, 1, ?, ?, ?, '2026-01-01 00:00:00')",
                 (enc("patDesktopKey"), enc("hunter2"), enc(totp)))
    conn.execute("INSERT INTO user_preferences VALUES (1, 1, 5, 15, 1, 21, 30, '2026-01-01 00:00:00')")
    conn.execute("INSERT INTO ticket_submissions VALUES (1, 1, '2026-02-12 21:58:53.695901', 'recA', "
                 "'Session A', 'Nakasuk School', 'Teacher', 'RITM001', '2026-02-20 15:00:00', 60, 'success')")
    conn.execute("INSERT INTO conflict_email_log VALUES (1, 1, 'recB', 'recC', '2026-02-13 10:00:00')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def desktop_db(tmp_path):
    return make_desktop_db(str(tmp_path / "gn_ticket.db"))


def _history():
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == EMAIL)).scalar_one()
        tickets = db.execute(select(TicketSubmission).where(TicketSubmission.user_id == user.id)).scalars().all()
        emailed = db.execute(select(ConflictEmailLog).where(ConflictEmailLog.user_id == user.id)).scalars().all()
        return tickets, emailed


def test_credentials_preferences_and_history_come_across(desktop_db):
    result = import_desktop_profile(desktop_db, EMAIL, name="Lead")

    profile = user_manager.load_profile(EMAIL)
    assert profile["airtable_api_key"] == "patDesktopKey"
    assert profile["servicenow_password"] == "hunter2"
    assert profile["totp_secret"] == "JBSWY3DPEHPK3PXP"
    assert profile["preferences"]["buffer_before"] == 5
    assert profile["preferences"]["buffer_after"] == 15
    assert profile["preferences"]["window_past_days"] == 21
    assert profile["preferences"]["window_future_days"] == 30

    tickets, emailed = _history()
    assert [t.ticket_id for t in tickets] == ["RITM001"]
    assert [(e.session_id, e.conflict_session_id) for e in emailed] == [("recB", "recC")]
    assert result["submissions"] == 1 and result["conflict_emails"] == 1


def test_automatic_booking_starts_off_so_desktop_and_web_never_both_book(desktop_db):
    import_desktop_profile(desktop_db, EMAIL)
    assert user_manager.load_profile(EMAIL)["preferences"]["auto_booking_enabled"] is False


def test_it_is_encrypted_here_under_this_service_key_not_the_desktop_one(desktop_db):
    import_desktop_profile(desktop_db, EMAIL)
    from models import UserCredential
    with SessionLocal() as db:
        stored = db.execute(select(UserCredential)).scalar_one().totp_secret_enc
    with pytest.raises(Exception):
        Fernet(DESKTOP_KEY.encode()).decrypt(stored.encode())


def test_importing_twice_does_not_duplicate_history(desktop_db):
    import_desktop_profile(desktop_db, EMAIL)
    import_desktop_profile(desktop_db, EMAIL, replace=True)
    tickets, emailed = _history()
    assert len(tickets) == 1 and len(emailed) == 1


def test_it_will_not_overwrite_saved_settings_unless_asked(desktop_db):
    user_manager.upsert_user(EMAIL, "Lead")
    user_manager.save_profile(EMAIL, {"airtable_api_key": "patWebKey", "servicenow_password": "web",
                                      "totp_secret": "WEBSECRET"})

    with pytest.raises(DesktopImportError, match="already have settings"):
        import_desktop_profile(desktop_db, EMAIL)
    assert user_manager.load_profile(EMAIL)["airtable_api_key"] == "patWebKey"

    import_desktop_profile(desktop_db, EMAIL, replace=True)
    assert user_manager.load_profile(EMAIL)["airtable_api_key"] == "patDesktopKey"


def test_someone_elses_file_imports_nothing(tmp_path):
    path = make_desktop_db(str(tmp_path / "theirs.db"), email="someone.else@takingitglobal.org")
    with pytest.raises(DesktopImportError, match="no saved settings for lead@"):
        import_desktop_profile(path, EMAIL)
    assert user_manager.load_profile(EMAIL) is None


def test_email_case_from_the_desktop_file_does_not_matter(tmp_path):
    path = make_desktop_db(str(tmp_path / "gn_ticket.db"), email="Lead@TakingItGlobal.org")
    import_desktop_profile(path, EMAIL)
    assert user_manager.is_profile_complete(EMAIL)


def test_a_file_from_an_older_build_with_a_different_key_still_imports(tmp_path, monkeypatch):
    """Builds handed out at different times may carry different keys; list them all."""
    older_key = Fernet.generate_key().decode()
    monkeypatch.setenv("DESKTOP_APP_ENCRYPTION_KEY", f"{DESKTOP_KEY},{older_key}")
    path = make_desktop_db(str(tmp_path / "gn_ticket.db"), key=older_key)

    import_desktop_profile(path, EMAIL)
    assert user_manager.load_profile(EMAIL)["airtable_api_key"] == "patDesktopKey"


def test_a_file_encrypted_with_an_unknown_key_says_so(tmp_path):
    path = make_desktop_db(str(tmp_path / "gn_ticket.db"), key=Fernet.generate_key().decode())
    with pytest.raises(DesktopImportError, match="could not be unlocked"):
        import_desktop_profile(path, EMAIL)


def test_an_unfinished_desktop_setup_is_not_imported(tmp_path):
    path = make_desktop_db(str(tmp_path / "gn_ticket.db"), totp="")
    with pytest.raises(DesktopImportError, match="never finished"):
        import_desktop_profile(path, EMAIL)


def test_a_file_that_is_not_a_database_is_turned_away(tmp_path):
    path = tmp_path / "notes.db"
    path.write_text("not a database")
    with pytest.raises(DesktopImportError, match="isn't a desktop app database"):
        import_desktop_profile(str(path), EMAIL)


def test_the_whole_folder_can_be_sent_and_the_database_found_in_it(tmp_path, desktop_db):
    """Nobody has to know which file matters: the folder is dropped in as it is."""
    junk = [tmp_path / "app.log", tmp_path / "error.log", tmp_path / "templates.html"]
    for path in junk:
        path.write_text("nothing useful in here")

    result = import_desktop_files([str(p) for p in junk] + [desktop_db], EMAIL)

    assert result["submissions"] == 1
    assert user_manager.is_profile_complete(EMAIL)


def test_the_key_in_the_folders_own_env_opens_a_file_the_server_key_cannot(tmp_path, monkeypatch):
    """An install older than the key on the server still imports: its .env came too."""
    their_key = Fernet.generate_key().decode()
    monkeypatch.setenv("DESKTOP_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    database = make_desktop_db(str(tmp_path / "gn_ticket.db"), key=their_key)
    env = tmp_path / ".env"
    env.write_text(f"GOOGLE_CLIENT_ID=123.apps.googleusercontent.com\nAPP_ENCRYPTION_KEY={their_key}\n")

    import_desktop_files([str(env), database], EMAIL)

    assert user_manager.load_profile(EMAIL)["totp_secret"] == "JBSWY3DPEHPK3PXP"


def test_sending_a_folder_with_no_settings_in_it_says_what_to_do(tmp_path):
    (tmp_path / "holiday.jpg").write_bytes(b"\xff\xd8\xff nothing here")

    with pytest.raises(DesktopImportError, match="whole GN_Ticket_Automator folder"):
        import_desktop_files([str(tmp_path / "holiday.jpg")], EMAIL)


@pytest.fixture
def client():
    user_manager.upsert_user(EMAIL, "Lead")
    main.app.config["TESTING"] = True
    main.app.secret_key = "test-secret"
    test_client = main.app.test_client()
    with test_client.session_transaction() as session:
        session["user"] = {"email": EMAIL, "name": "Lead"}
    return test_client


def test_dropping_the_folder_on_the_page(client, desktop_db, tmp_path):
    with open(desktop_db, "rb") as handle:
        database = handle.read()
    response = client.post("/import-desktop", content_type="multipart/form-data", data={"desktop_files": [
        (io.BytesIO(b"log line"), "app.log"),
        (io.BytesIO(database), "gn_ticket.db"),
    ]})

    assert response.status_code == 200
    assert b"Your settings are here" in response.data
    assert b"automatic booking is switched off" in response.data
    assert user_manager.is_profile_complete(EMAIL)


def test_the_page_says_what_to_do_when_the_wrong_thing_is_sent(client):
    response = client.post("/import-desktop", content_type="multipart/form-data",
                           data={"desktop_files": [(io.BytesIO(b"nope"), "notes.txt")]})
    assert b"whole GN_Ticket_Automator folder" in response.data


def test_the_page_explains_where_to_find_the_folder(client):
    response = client.get("/import-desktop")
    assert b"GN_Ticket_Automator" in response.data
    assert b"Home" in response.data


def test_new_users_are_pointed_at_the_import(client):
    response = client.get("/setup")
    assert b"/import-desktop" in response.data
