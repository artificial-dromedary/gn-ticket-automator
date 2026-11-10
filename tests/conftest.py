"""Test environment: a scratch SQLite database and a throwaway encryption key.

Both must be set before db.py / user_profiles.py are imported, since they read the
environment at import time.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB_FILE = os.path.join(tempfile.mkdtemp(prefix="gn_test_"), "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_FILE}")
os.environ.setdefault("APP_ENCRYPTION_KEY", "0123456789abcdef0123456789abcdef")
# Scan tests are about holding conflicts back; the tests for writing to teachers
# automatically switch it on themselves.
os.environ.setdefault("AUTO_SEND_CONFLICT_EMAILS", "0")

import pytest  # noqa: E402

from db import Base, engine, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    """Every test starts against empty tables, built the way the entrypoints build them."""
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)
