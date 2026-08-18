from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from conflict import check_for_time_conflicts
from db import SessionLocal
from models import TicketSubmission, User
from ticket_submission_log import TicketSubmissionLog
from user_profiles import user_manager


USER_EMAIL = 'user@example.com'


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


def _backdate(session_id, days):
    """Push a submission's timestamp into the past so pruning can see it."""
    with SessionLocal() as db:
        db.execute(
            update(TicketSubmission)
            .where(TicketSubmission.session_id == session_id)
            .values(submitted_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days))
        )
        db.commit()


def test_ticket_submission_log_prunes_old_entries():
    user_manager.upsert_user(USER_EMAIL, name='Test User')
    log = TicketSubmissionLog(retention_days=30)
    now = datetime.now(timezone.utc)

    log.add_successful_submissions(USER_EMAIL, [{
        'session_id': 'old',
        'title': 'Old session',
        'school': 'Old School',
        'teacher': 'T',
        'ticket_id': 'REQ001',
        'start_time': now.isoformat(),
        'length': 60,
    }])
    _backdate('old', days=45)

    log.add_successful_submissions(USER_EMAIL, [{
        'session_id': 'new',
        'title': 'New Session',
        'school': 'New School',
        'teacher': 'T',
        'ticket_id': 'REQ002',
        'start_time': now.isoformat(),
        'length': 45,
    }])

    entries = log.get_entries(USER_EMAIL)
    assert len(entries) == 1
    assert entries[0]['ticket_id'] == 'REQ002'

    with SessionLocal() as db:
        remaining = db.execute(select(TicketSubmission.session_id)).scalars().all()
    assert remaining == ['new']


def test_submissions_are_scoped_to_their_user():
    user_manager.upsert_user(USER_EMAIL, name='Test User')
    user_manager.upsert_user('other@example.com', name='Other User')
    log = TicketSubmissionLog()
    now = datetime.now(timezone.utc)

    log.add_successful_submissions(USER_EMAIL, [{
        'session_id': 'mine', 'title': 'Mine', 'school': 'S', 'teacher': 'T',
        'ticket_id': 'REQ100', 'start_time': now.isoformat(), 'length': 60,
    }])
    log.add_successful_submissions('other@example.com', [{
        'session_id': 'theirs', 'title': 'Theirs', 'school': 'S', 'teacher': 'T',
        'ticket_id': 'REQ200', 'start_time': now.isoformat(), 'length': 60,
    }])

    assert [e['ticket_id'] for e in log.get_entries(USER_EMAIL)] == ['REQ100']
    assert [e['ticket_id'] for e in log.get_entries('other@example.com')] == ['REQ200']


def test_submissions_for_unknown_user_are_ignored():
    log = TicketSubmissionLog()
    log.add_successful_submissions('nobody@example.com', [{
        'session_id': 'x', 'title': 'X', 'school': 'S', 'teacher': 'T',
        'ticket_id': 'REQ300', 'start_time': None, 'length': 60,
    }])

    with SessionLocal() as db:
        assert db.execute(select(User)).scalars().all() == []
        assert db.execute(select(TicketSubmission)).scalars().all() == []


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
