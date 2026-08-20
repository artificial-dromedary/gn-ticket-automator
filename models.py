from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.orm import relationship
from db import Base


def utcnow():
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255))
    picture_url = Column(String(1024))
    created_at = Column(DateTime, default=utcnow)
    last_login_at = Column(DateTime, default=utcnow)

    credentials = relationship("UserCredential", uselist=False, back_populates="user", cascade="all, delete-orphan")
    preferences = relationship("UserPreference", uselist=False, back_populates="user", cascade="all, delete-orphan")


class UserCredential(Base):
    __tablename__ = "user_credentials"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    airtable_api_key_enc = Column(Text)
    servicenow_password_enc = Column(Text)
    totp_secret_enc = Column(Text)
    updated_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="credentials")


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    buffer_before = Column(Integer, default=10)
    buffer_after = Column(Integer, default=10)
    auto_booking_enabled = Column(Boolean, default=False)
    # How often the scheduled scan should run for this user. The cron job fires
    # hourly and skips whoever is not due yet, so this is per-person.
    scan_frequency_hours = Column(Integer, default=24)
    window_past_days = Column(Integer, default=14)
    window_future_days = Column(Integer, default=90)
    updated_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="preferences")


class TicketSubmission(Base):
    __tablename__ = "ticket_submissions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    submitted_at = Column(DateTime, default=utcnow, index=True)
    session_id = Column(String(255))
    title = Column(String(512))
    school = Column(String(512))
    teacher = Column(String(512))
    ticket_id = Column(String(255))
    start_time = Column(DateTime)
    length = Column(Integer, default=0)
    status = Column(String(64), default="success")


class ConflictEmailLog(Base):
    """Records when a conflict notification email was sent for a session."""
    __tablename__ = "conflict_email_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String(255), nullable=False, index=True)   # candidate session s_id
    conflict_session_id = Column(String(255))                       # the Airtable conflict session id
    emailed_at = Column(DateTime, default=utcnow)


class TaskLock(Base):
    """Cross-process mutex.

    Lives in the database rather than Redis so it holds in every deployment shape —
    the cron-job architecture has no broker at all, and a lock that silently grants
    when its backend is missing is worse than no lock, because it reads as one.
    """
    __tablename__ = "task_locks"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    token = Column(String(64), nullable=False)
    acquired_at = Column(DateTime, default=utcnow, nullable=False)
    # A holder that dies mid-run would otherwise block its user forever.
    expires_at = Column(DateTime, nullable=False, index=True)


class ScanRequest(Base):
    """A person asked for a scan sooner than their interval would give them.

    The cron job's command is fixed, so an on-demand trigger cannot pass a flag.
    A row here is the flag: the next run treats this user as due whatever their
    interval says, and clears it once scanned.
    """
    __tablename__ = "scan_requests"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    requested_at = Column(DateTime, default=utcnow, nullable=False)


class DailySummaryLog(Base):
    """One row per user per day the summary was sent.

    The job that sends it fires hourly, so without this the 5pm digest would go
    out again at 6pm, 7pm, and every hour until midnight.
    """
    __tablename__ = "daily_summary_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    summary_date = Column(String(10), nullable=False, index=True)  # local YYYY-MM-DD
    sent_at = Column(DateTime, default=utcnow, nullable=False)


class ScanResult(Base):
    __tablename__ = "scan_results"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scanned_at = Column(DateTime, default=utcnow, index=True)
    conflicts_json = Column(Text)
    candidate_ids = Column(Text)
    summary = Column(Text)
