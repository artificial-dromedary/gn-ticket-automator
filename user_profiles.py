import json
import os
import sqlite3
from cryptography.fernet import Fernet
import keyring
import base64
from datetime import datetime


class UserProfileManager:
    def __init__(self, db_path=None):
        if db_path is None:
            # Use app data directory for database
            from pathlib import Path
            import sys
            if getattr(sys, 'frozen', False):
                app_dir = Path.home() / 'GN_Ticket_Automator'
            else:
                app_dir = Path(__file__).parent
            app_dir.mkdir(exist_ok=True)
            db_path = app_dir / "user_profiles.db"
        self.db_path = db_path
        self.service_name = "gn_ticket_automator"
        self.init_database()

    def init_database(self):
        """Initialize the user profiles database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_profiles (
                email TEXT PRIMARY KEY,
                created_at TEXT,
                updated_at TEXT,
                airtable_api_key_encrypted TEXT,
                servicenow_password_encrypted TEXT,
                totp_secret_encrypted TEXT,
                chatgpt_api_key_encrypted TEXT,
                preferences TEXT
            )
        ''')

        conn.commit()
        conn.close()

        # Add the new column if it doesn't exist (for existing databases)
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('ALTER TABLE user_profiles ADD COLUMN chatgpt_api_key_encrypted TEXT')
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass  # Column already exists

    def _get_encryption_key(self, email):
        """Get or create encryption key for user from system keyring."""
        key_name = f"encryption_key_{email}"
        try:
            # Try to get existing key from keyring
            key_b64 = keyring.get_password(self.service_name, key_name)
            if key_b64:
                return key_b64.encode()
            else:
                # Generate new key and store it
                key = Fernet.generate_key()
                keyring.set_password(self.service_name, key_name, key.decode())
                return key
        except Exception as e:
            print(f"Warning: Keyring access failed, using fallback encryption: {e}")
            # Fallback: use a simple key derived from email (less secure but stable)
            import hashlib
            hash_digest = hashlib.sha256(email.encode()).digest()
            fernet_key = base64.urlsafe_b64encode(hash_digest[:32])
            return fernet_key

    def _encrypt_data(self, data, email):
        """Encrypt sensitive data"""
        if not data:
            return None
        key = self._get_encryption_key(email)
        f = Fernet(key)
        return f.encrypt(data.encode()).decode()

    def _decrypt_data(self, encrypted_data, email):
        """Decrypt sensitive data"""
        if not encrypted_data:
            return None
        try:
            key = self._get_encryption_key(email)
            f = Fernet(key)
            return f.decrypt(encrypted_data.encode()).decode()
        except Exception:
            # If decryption fails (e.g., key changed), return the raw encrypted data.
            # The main app logic will detect this "garbled" text and prompt the user.
            return encrypted_data

    def save_profile(self, email, profile_data):
        """Save user profile with encryption"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        airtable_key_enc = self._encrypt_data(profile_data.get('airtable_api_key'), email)
        servicenow_pw_enc = self._encrypt_data(profile_data.get('servicenow_password'), email)
        totp_secret_enc = self._encrypt_data(profile_data.get('totp_secret'), email)
        chatgpt_key_enc = self._encrypt_data(profile_data.get('chatgpt_api_key'), email)

        preferences = json.dumps(profile_data.get('preferences', {}))
        now = datetime.now().isoformat()

        cursor.execute('''
            INSERT OR REPLACE INTO user_profiles 
            (email, created_at, updated_at, airtable_api_key_encrypted, 
             servicenow_password_encrypted, totp_secret_encrypted, chatgpt_api_key_encrypted, preferences)
            VALUES (?, 
                    COALESCE((SELECT created_at FROM user_profiles WHERE email = ?), ?),
                    ?, ?, ?, ?, ?)
        ''', (email, email, now, now, airtable_key_enc, servicenow_pw_enc, totp_secret_enc, preferences))

        conn.commit()
        conn.close()

    def load_profile(self, email):
        """Load and decrypt user profile"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT airtable_api_key_encrypted, servicenow_password_encrypted, 
                   totp_secret_encrypted, chatgpt_api_key_encrypted, preferences
            FROM user_profiles WHERE email = ?
        ''', (email,))

        result = cursor.fetchone()
        conn.close()

        if not result:
            return None

        airtable_key_enc, servicenow_pw_enc, totp_secret_enc, chatgpt_key_enc, preferences_json = result

        profile = {
            'airtable_api_key': self._decrypt_data(airtable_key_enc, email),
            'servicenow_password': self._decrypt_data(servicenow_pw_enc, email),
            'totp_secret': self._decrypt_data(totp_secret_enc, email),
            'chatgpt_api_key': self._decrypt_data(chatgpt_key_enc, email),
            'preferences': json.loads(preferences_json) if preferences_json else {},
        }

        return profile

    def is_profile_complete(self, email):
        """Check if user profile has all required fields"""
        profile = self.load_profile(email)
        if not profile:
            return False

        required_fields = ['airtable_api_key', 'servicenow_password', 'totp_secret']
        return all(profile.get(field) for field in required_fields)


# Global instance
user_manager = UserProfileManager()