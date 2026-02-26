from datetime import datetime, timedelta, timezone
from sqlalchemy import select, delete

from db import SessionLocal, Base, engine
from models import TicketSubmission, User


Base.metadata.create_all(bind=engine)


class TicketSubmissionLog:
    """Persisted log of submitted GN tickets retained for a rolling window."""

    def __init__(self, retention_days=365):
        self.retention_days = retention_days

    def _utcnow(self):
        return datetime.now(timezone.utc)

    def prune(self):
        cutoff = self._utcnow() - timedelta(days=self.retention_days)
        with SessionLocal() as db:
            db.execute(delete(TicketSubmission).where(TicketSubmission.submitted_at < cutoff))
            db.commit()

    def get_entries(self, user_email=None, window_past_days=None):
        self.prune()
        with SessionLocal() as db:
            stmt = select(TicketSubmission).order_by(TicketSubmission.submitted_at.desc())
            if user_email:
                user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
                if not user:
                    return []
                stmt = stmt.where(TicketSubmission.user_id == user.id)
            if window_past_days is not None:
                cutoff = self._utcnow() - timedelta(days=int(window_past_days))
                stmt = stmt.where(TicketSubmission.submitted_at >= cutoff)
            rows = db.execute(stmt).scalars().all()

        entries = []
        for row in rows:
            entries.append({
                "submitted_at": row.submitted_at.replace(tzinfo=timezone.utc).isoformat() if row.submitted_at else None,
                "submitted_by": user_email,
                "session_id": row.session_id,
                "title": row.title,
                "school": row.school,
                "teacher": row.teacher,
                "ticket_id": row.ticket_id,
                "start_time": row.start_time.replace(tzinfo=timezone.utc).isoformat() if row.start_time else None,
                "length": row.length,
            })
        return entries

    def add_successful_submissions(self, user_email, successful_sessions):
        if not successful_sessions:
            return
        created_at = self._utcnow()
        with SessionLocal() as db:
            user = db.execute(select(User).where(User.email == user_email.strip().lower())).scalar_one_or_none()
            if not user:
                return
            for successful in successful_sessions:
                start_time = None
                try:
                    if successful.get("start_time"):
                        start_time = datetime.fromisoformat(successful.get("start_time"))
                        if start_time.tzinfo is None:
                            start_time = start_time.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    start_time = None
                row = TicketSubmission(
                    user_id=user.id,
                    submitted_at=created_at,
                    session_id=successful.get("session_id"),
                    title=successful.get("title", "Unknown Session"),
                    school=successful.get("school", "Unknown School"),
                    teacher=successful.get("teacher", "Unknown Teacher"),
                    ticket_id=successful.get("ticket_id", "Unknown"),
                    start_time=start_time,
                    length=successful.get("length", 0) or 0,
                    status="success",
                )
                db.add(row)
            db.commit()


def parse_log_start_and_end(entry):
    """Return (start, end) datetimes for a log entry or (None, None) if invalid."""
    start_raw = entry.get("start_time")
    if not start_raw:
        return None, None

    try:
        start_time = datetime.fromisoformat(start_raw)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None, None

    try:
        length = int(entry.get("length", 0) or 0)
    except (TypeError, ValueError):
        length = 0

    end_time = start_time + timedelta(minutes=length)
    return start_time, end_time
