from datetime import datetime, timedelta, timezone

from main import check_for_time_conflicts
from ticket_submission_log import TicketSubmissionLog


class DummySession:
    def __init__(self, s_id, title, school, start_time, length=60, status='Booked', teacher='Teacher', gn_requested=False):
        self.s_id = s_id
        self.title = title
        self.school = school
        self.start_time = start_time
        self.length = length
        self.status = status
        self.teacher = teacher
        self.gn_ticket_requested = gn_requested
        self.is_conflict = False
        self.conflict_details = ''
        self.conflict_type = None


def test_ticket_submission_log_prunes_old_entries(tmp_path):
    log = TicketSubmissionLog(tmp_path / 'ticket_log.json', retention_days=30)

    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(days=45)).isoformat()

    log._write_entries_unlocked([
        {
            'submitted_at': old_time,
            'submitted_by': 'user@example.com',
            'session_id': 'old',
            'title': 'Old session',
            'school': 'Old School',
            'ticket_id': 'REQ001',
            'start_time': now.isoformat(),
            'length': 60,
        }
    ])

    log.add_successful_submissions('user@example.com', [
        {
            'session_id': 'new',
            'title': 'New Session',
            'school': 'New School',
            'teacher': 'T',
            'ticket_id': 'REQ002',
            'start_time': now.isoformat(),
            'length': 45,
        }
    ])

    entries = log.get_entries('user@example.com')
    assert len(entries) == 1
    assert entries[0]['ticket_id'] == 'REQ002'


def test_conflict_detection_flags_ghost_ticket_overlap():
    start = datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc)
    candidate = DummySession('cand-1', 'Current booking', 'School A', start, length=60)

    historical_entries = [
        {
            'session_id': 'different-session',
            'title': 'Original booked time',
            'school': 'School A',
            'ticket_id': 'REQ12345',
            'start_time': (start + timedelta(minutes=30)).isoformat(),
            'length': 60,
            'submitted_at': datetime.now(timezone.utc).isoformat(),
        }
    ]

    result = check_for_time_conflicts([candidate], [], historical_entries)
    assert result[0].is_conflict is True
    assert result[0].conflict_type == 'ghost_ticket'
    assert 'Rebooked/ghost ticket' in result[0].conflict_details
    assert 'REQ12345' in result[0].conflict_details
    assert result[0].conflict_start_iso == (start + timedelta(minutes=30)).isoformat()
    assert result[0].conflict_end_iso == (start + timedelta(minutes=90)).isoformat()
