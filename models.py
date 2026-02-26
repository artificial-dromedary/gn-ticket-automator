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


class ScanResult(Base):
    __tablename__ = "scan_results"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scanned_at = Column(DateTime, default=utcnow, index=True)
    conflicts_json = Column(Text)
    candidate_ids = Column(Text)
    summary = Column(Text)
