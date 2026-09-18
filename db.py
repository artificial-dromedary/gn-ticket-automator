import logging
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///gn_ticket.db"))

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

Base = declarative_base()


def ensure_column(table, column, ddl_type, default_sql=None):
    """Add a column that create_all() cannot.

    Base.metadata.create_all() creates missing tables but never alters existing
    ones, so a new column on a table that already exists in production is invisible
    to it. This covers that gap without pulling in a migration framework: it is
    idempotent and works on both Postgres and SQLite.
    """
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return False  # create_all will build it with the column already present
    if column in {c["name"] for c in inspector.get_columns(table)}:
        return False

    statement = f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
    if default_sql is not None:
        statement += f" DEFAULT {default_sql}"

    with engine.begin() as connection:
        connection.execute(text(statement))
    logging.info("Added missing column %s.%s", table, column)
    return True


def init_db():
    """Bring the schema and stored data up to date. Called once per process, by
    the entrypoints, never on import.

    Importing a module used to run this as a side effect, which meant any script
    or test that touched `user_profiles` also migrated whatever database the
    environment pointed at. Now the web app and the cron job call it explicitly
    at startup, and nothing else does.
    """
    # Registers every table on Base before create_all looks at it.
    import models  # noqa: F401
    from sqlalchemy import update as sa_update

    Base.metadata.create_all(bind=engine)

    # Columns added after their table shipped, which create_all cannot add.
    ensure_column("user_preferences", "scan_frequency_hours", "INTEGER", "24")
    ensure_column("user_preferences", "notification_email", "VARCHAR(255)")

    # Scan intervals that used to be offered, moved to what replaced them. Leaving
    # a retired value in place would render the dashboard's dropdown with nothing
    # selected, and the next save would silently pick for the person.
    from models import UserPreference
    from user_profiles import RETIRED_SCAN_FREQUENCIES

    with SessionLocal() as db:
        for retired, replacement in RETIRED_SCAN_FREQUENCIES.items():
            db.execute(
                sa_update(UserPreference)
                .where(UserPreference.scan_frequency_hours == retired)
                .values(scan_frequency_hours=replacement)
            )
        db.commit()
