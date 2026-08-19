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
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/15")

import pytest  # noqa: E402

import models  # noqa: E402,F401  (registers tables on Base)
from db import Base, engine  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    """Every test starts against empty tables."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
