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
    idempotent, safe to run at every import, and works on both Postgres and SQLite.
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
