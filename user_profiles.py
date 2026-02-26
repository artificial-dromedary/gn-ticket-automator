import base64
import os
from datetime import datetime
from typing import Optional
from cryptography.fernet import Fernet
from sqlalchemy import select

from db import SessionLocal, Base, engine
from models import User, UserCredential, UserPreference


Base.metadata.create_all(bind=engine)


def _get_fernet():
    key = os.getenv("APP_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("APP_ENCRYPTION_KEY is required for server-side encryption.")

    # Accept raw 32-byte key or urlsafe base64-encoded key
    try:
        if len(key) == 32:
            key = base64.urlsafe_b64encode(key.encode())
        else:
            key = key.encode()
        return Fernet(key)
    except Exception as exc:
        raise RuntimeError("Invalid APP_ENCRYPTION_KEY format.") from exc


class UserProfileManager:
    def __init__(self):
        self._fernet = _get_fernet()

    def _encrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return self._fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return self._fernet.decrypt(value.encode()).decode()

    def upsert_user(self, email, name=None, picture_url=None):
        email = email.strip().lower()
        with SessionLocal() as db:
            user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            now = datetime.utcnow()
            if not user:
                user = User(email=email, name=name, picture_url=picture_url, created_at=now, last_login_at=now)
                db.add(user)
            else:
                user.name = name or user.name
                user.picture_url = picture_url or user.picture_url
                user.last_login_at = now
            db.commit()
            db.refresh(user)
            return user

    def save_profile(self, email, profile_data):
        email = email.strip().lower()
        with SessionLocal() as db:
            user = db.execute(select(User).where(User.email == email)).scalar_one()
            creds = db.execute(select(UserCredential).where(UserCredential.user_id == user.id)).scalar_one_or_none()
            if not creds:
                creds = UserCredential(user_id=user.id)
                db.add(creds)

            creds.airtable_api_key_enc = self._encrypt(profile_data.get("airtable_api_key"))
            creds.servicenow_password_enc = self._encrypt(profile_data.get("servicenow_password"))
            creds.totp_secret_enc = self._encrypt(profile_data.get("totp_secret"))
            creds.updated_at = datetime.utcnow()

            prefs = profile_data.get("preferences") or {}
            self._save_preferences(db, user.id, prefs)

            db.commit()

    def _save_preferences(self, db, user_id, prefs):
        preferences = db.execute(select(UserPreference).where(UserPreference.user_id == user_id)).scalar_one_or_none()
        if not preferences:
            preferences = UserPreference(user_id=user_id)
            db.add(preferences)

        if "buffer_before" in prefs:
            preferences.buffer_before = int(prefs.get("buffer_before") or preferences.buffer_before)
        if "buffer_after" in prefs:
            preferences.buffer_after = int(prefs.get("buffer_after") or preferences.buffer_after)
        if "auto_booking_enabled" in prefs:
            preferences.auto_booking_enabled = bool(prefs.get("auto_booking_enabled"))
        if "window_past_days" in prefs:
            preferences.window_past_days = int(prefs.get("window_past_days") or preferences.window_past_days)
        if "window_future_days" in prefs:
            preferences.window_future_days = int(prefs.get("window_future_days") or preferences.window_future_days)
        preferences.updated_at = datetime.utcnow()

    def load_profile(self, email):
        email = email.strip().lower()
        with SessionLocal() as db:
            user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if not user:
                return None
            creds = db.execute(select(UserCredential).where(UserCredential.user_id == user.id)).scalar_one_or_none()
            if not creds:
                return None

            prefs = db.execute(select(UserPreference).where(UserPreference.user_id == user.id)).scalar_one_or_none()

            return {
                "airtable_api_key": self._decrypt(creds.airtable_api_key_enc),
                "servicenow_password": self._decrypt(creds.servicenow_password_enc),
                "totp_secret": self._decrypt(creds.totp_secret_enc),
                "preferences": {
                    "buffer_before": prefs.buffer_before if prefs else 10,
                    "buffer_after": prefs.buffer_after if prefs else 10,
                    "auto_booking_enabled": prefs.auto_booking_enabled if prefs else False,
                    "window_past_days": prefs.window_past_days if prefs else 14,
                    "window_future_days": prefs.window_future_days if prefs else 90,
                }
            }

    def get_preferences(self, email):
        email = email.strip().lower()
        with SessionLocal() as db:
            user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if not user:
                return None
            prefs = db.execute(select(UserPreference).where(UserPreference.user_id == user.id)).scalar_one_or_none()
            if not prefs:
                return {
                    "buffer_before": 10,
                    "buffer_after": 10,
                    "auto_booking_enabled": False,
                    "window_past_days": 14,
                    "window_future_days": 90,
                }
            return {
                "buffer_before": prefs.buffer_before,
                "buffer_after": prefs.buffer_after,
                "auto_booking_enabled": prefs.auto_booking_enabled,
                "window_past_days": prefs.window_past_days,
                "window_future_days": prefs.window_future_days,
            }

    def update_preferences(self, email, prefs):
        email = email.strip().lower()
        with SessionLocal() as db:
            user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if not user:
                return
            self._save_preferences(db, user.id, prefs)
            db.commit()

    def is_profile_complete(self, email):
        profile = self.load_profile(email)
        if not profile:
            return False
        required_fields = ["airtable_api_key", "servicenow_password", "totp_secret"]
        return all(profile.get(field) for field in required_fields)

    def list_auto_enabled_users(self):
        with SessionLocal() as db:
            results = db.execute(
                select(User).join(UserPreference).where(UserPreference.auto_booking_enabled.is_(True))
            ).scalars().all()
            return results

    def get_user_by_email(self, email):
        email = email.strip().lower()
        with SessionLocal() as db:
            return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


user_manager = UserProfileManager()
