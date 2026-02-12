import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


class TicketSubmissionLog:
    """Persisted log of submitted GN tickets retained for a rolling 30-day window."""

    def __init__(self, log_path, retention_days=30):
        self.log_path = Path(log_path)
        self.retention_days = retention_days
        self._lock = threading.Lock()

    def _utcnow(self):
        return datetime.now(timezone.utc)

    def _parse_iso(self, value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (ValueError, TypeError):
            return None

    def _read_entries_unlocked(self):
        if not self.log_path.exists():
            return []

        try:
            with self.log_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

        if isinstance(data, dict):
            data = data.get('entries', [])

        return data if isinstance(data, list) else []

    def _write_entries_unlocked(self, entries):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('w', encoding='utf-8') as f:
            json.dump({'entries': entries}, f, indent=2)

    def _is_recent(self, entry):
        submitted_at = self._parse_iso(entry.get('submitted_at'))
        if not submitted_at:
            return False
        cutoff = self._utcnow() - timedelta(days=self.retention_days)
        return submitted_at >= cutoff

    def prune(self):
        with self._lock:
            entries = self._read_entries_unlocked()
            recent_entries = [entry for entry in entries if self._is_recent(entry)]
            self._write_entries_unlocked(recent_entries)
            return recent_entries

    def get_entries(self, user_email=None):
        entries = self.prune()
        if not user_email:
            return entries
        email = user_email.strip().lower()
        return [entry for entry in entries if (entry.get('submitted_by') or '').strip().lower() == email]

    def add_successful_submissions(self, user_email, successful_sessions):
        if not successful_sessions:
            return

        created_at = self._utcnow().isoformat()
        entries_to_add = []

        for successful in successful_sessions:
            entries_to_add.append({
                'submitted_at': created_at,
                'submitted_by': user_email,
                'session_id': successful.get('session_id'),
                'title': successful.get('title', 'Unknown Session'),
                'school': successful.get('school', 'Unknown School'),
                'teacher': successful.get('teacher', 'Unknown Teacher'),
                'ticket_id': successful.get('ticket_id', 'Unknown'),
                'start_time': successful.get('start_time'),
                'length': successful.get('length', 0),
            })

        with self._lock:
            entries = self._read_entries_unlocked()
            entries.extend(entries_to_add)
            entries = [entry for entry in entries if self._is_recent(entry)]
            self._write_entries_unlocked(entries)


def parse_log_start_and_end(entry):
    """Return (start, end) datetimes for a log entry or (None, None) if invalid."""
    start_raw = entry.get('start_time')
    if not start_raw:
        return None, None

    try:
        start_time = datetime.fromisoformat(start_raw)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None, None

    try:
        length = int(entry.get('length', 0) or 0)
    except (TypeError, ValueError):
        length = 0

    end_time = start_time + timedelta(minutes=length)
    return start_time, end_time
