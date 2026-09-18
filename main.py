"""The web app: sign-in, the dashboard, and the settings behind it.

Booking itself lives in tasks.py and normally runs in the hourly cron job. This
process only drives a browser where GN_ENABLE_MANUAL_BOOKING says it has the
memory to.
"""
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import jwt
from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, session, url_for
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from google_auth_oauthlib.flow import Flow
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from db import DATABASE_URL, SessionLocal, init_db  # noqa: E402
from desktop_import import (MAX_UPLOAD_BYTES, MAX_UPLOAD_FILES, DesktopImportError,  # noqa: E402
                            import_desktop_files)
from airtable_integration import create_airtable_client  # noqa: E402
from emailer import (DISPLAY_TZ, friendly_datetime, send_booking_summary_email,  # noqa: E402
                     teacher_conflict_email, teacher_conflict_recipients,
                     teacher_conflict_subject)
from models import ScanResult, User, ConflictEmailLog  # noqa: E402
import gn_ticket  # noqa: E402
import render_api  # noqa: E402
import tasks  # noqa: E402
from tasks import auto_scan_time_label, booking_in_progress, busy_notice, dispatch_scan  # noqa: E402
from ticket_submission_log import ticket_log  # noqa: E402
from user_profiles import (LOOKAHEAD_FOREVER_DAYS, LOOKAHEAD_STOPS, SCAN_FREQUENCY_CHOICES,  # noqa: E402
                           normalize_lookahead, normalize_scan_frequency, user_manager)

init_db()

app = Flask(__name__)


def load_config_from_env():
    config = {
        'GOOGLE_CLIENT_ID': os.getenv('GOOGLE_CLIENT_ID'),
        'GOOGLE_CLIENT_SECRET': os.getenv('GOOGLE_CLIENT_SECRET'),
        'OAUTH_REDIRECT_URI': os.getenv('OAUTH_REDIRECT_URI', 'http://127.0.0.1:5000/oauth/callback'),
        'ALLOWED_DOMAINS': os.getenv('ALLOWED_DOMAINS', 'takingitglobal.org').split(','),
        'SECRET_KEY': os.getenv('SECRET_KEY'),
    }
    is_valid = bool(config['GOOGLE_CLIENT_ID'] and config['GOOGLE_CLIENT_SECRET'])
    if not is_valid:
        # Names only. The values are secrets and this goes to the log.
        logging.error("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must both be set; sign-in is off.")
    return config, is_valid


CONFIG, CONFIG_VALID = load_config_from_env()

# The Google OAuth project is an unpublished internal one and the callback may be
# plain http in development, so oauthlib's https check is relaxed on purpose.
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Manual booking drives Chrome inside this process, which a small hosted instance
# cannot survive. Off there; on where the process has a machine's worth of memory.
MANUAL_BOOKING_ENABLED = os.getenv("GN_ENABLE_MANUAL_BOOKING", "true").strip().lower() in ("1", "true", "yes")

# Session configuration. Sessions live in the database, not on disk: the container
# filesystem is ephemeral, so a filesystem store signs everyone out on every deploy.
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    # This engine only carries session reads and writes; db.py has its own pool for
    # application queries. Keep it small so the two together stay well inside the
    # connection limit of a small Postgres instance.
    'pool_size': 2,
    'max_overflow': 3,
    'pool_pre_ping': True,
    'pool_recycle': 300,
}
app.config['SESSION_TYPE'] = 'sqlalchemy'
app.config['SESSION_SQLALCHEMY'] = SQLAlchemy(app)
app.config['SESSION_SQLALCHEMY_TABLE'] = 'flask_sessions'
# SQL has no TTL, so expired rows are pruned on roughly every Nth request.
app.config['SESSION_CLEANUP_N_REQUESTS'] = 200
# The cookie: never readable from script, never sent on a cross-site request, and
# only over https wherever the sign-in callback itself is https.
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv(
    'SESSION_COOKIE_SECURE', str(CONFIG['OAUTH_REDIRECT_URI'].startswith('https://'))
).strip().lower() in ('1', 'true', 'yes')
Session(app)

app.jinja_env.filters['friendly_datetime'] = friendly_datetime
# The pages re-render times in the browser, so the templates need the same zone the
# server-rendered ones use — otherwise the two disagree on a machine set elsewhere.
app.jinja_env.globals['display_timezone'] = DISPLAY_TZ
# Same wording as the conflict emails, so the dashboard and the inbox never disagree.
app.jinja_env.globals['teacher_conflict_email'] = teacher_conflict_email
app.jinja_env.globals['teacher_conflict_subject'] = teacher_conflict_subject
app.jinja_env.globals['teacher_conflict_recipients'] = teacher_conflict_recipients


@app.before_request
def refuse_cross_site_writes():
    """Every state-changing request must come from this site's own pages.

    There are no per-form CSRF tokens. Instead, the SameSite cookie above keeps the
    session out of cross-site requests, and this checks the headers every current
    browser sends so a forged POST from elsewhere is refused outright. A request
    with neither header (a non-browser client, the test client) is let through.
    """
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    fetch_site = request.headers.get('Sec-Fetch-Site')
    if fetch_site and fetch_site not in ('same-origin', 'none'):
        abort(403)
    origin = request.headers.get('Origin')
    if origin and urlparse(origin).netloc != request.host:
        abort(403)
    return None


# --- Booking progress -------------------------------------------------------
#
# Each manual booking run keeps a deque of its most recent progress entries, in
# this process. That is fine for the one-worker deployment this runs as; a second
# worker would not see the first one's runs.
PROGRESS_KEEP_SECONDS = int(os.getenv("GN_PROGRESS_KEEP_SECONDS", str(6 * 60 * 60)))
progress_store = {}      # session_id -> deque of entries
progress_owners = {}     # session_id -> (owner email, started at)
progress_counters = {}
progress_lock = threading.Lock()


def _forget_stale_progress():
    cutoff = time.monotonic() - PROGRESS_KEEP_SECONDS
    for session_id, (_, started) in list(progress_owners.items()):
        if started < cutoff:
            progress_store.pop(session_id, None)
            progress_owners.pop(session_id, None)
            progress_counters.pop(session_id, None)


def start_progress(session_id, owner_email):
    with progress_lock:
        _forget_stale_progress()
        progress_store[session_id] = deque(maxlen=200)
        progress_owners[session_id] = (owner_email, time.monotonic())
        progress_counters[session_id] = 0


def set_progress(session_id, message, step=None, total_steps=None, status="running", session_ref=None):
    """Record a progress entry for a booking run. A run nobody is watching (the
    scheduled one passes no id) records nothing."""
    if not session_id:
        return
    with progress_lock:
        if session_id not in progress_store:
            return
        progress_counters[session_id] += 1
        progress_store[session_id].append({
            'seq': progress_counters[session_id],
            'timestamp': datetime.now(ZoneInfo(DISPLAY_TZ)).strftime('%H:%M:%S'),
            'message': message,
            'step': step,
            'total_steps': total_steps,
            'status': status,
            'session_ref': session_ref,
        })


def get_progress(session_id, owner_email):
    """The entries for a run, or None when it is not this person's run."""
    with progress_lock:
        owner = progress_owners.get(session_id)
        if not owner or owner[0] != owner_email:
            return None
        return list(progress_store.get(session_id, deque()))


def require_auth(f):
    """Decorator to require authentication"""
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def notify_booking_finished(user_email, successful, failed, conflicts=None):
    """Email the result of a booking someone started by hand.

    Best-effort: a booking that worked must not be reported as a failure because the
    mail relay was down, and the outcome is on the progress page regardless.
    """
    try:
        send_booking_summary_email(user_manager.notification_email(user_email),
                                   successful, failed,
                                   conflict_sessions=conflicts, manual=True)
    except Exception as exc:
        logging.error("Could not send booking summary to %s: %s", user_email, exc)


def create_flow():
    """Create Google OAuth flow for web application"""
    if not CONFIG_VALID:
        return None

    client_config_dict = {
        "web": {
            "client_id": CONFIG['GOOGLE_CLIENT_ID'],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": CONFIG['GOOGLE_CLIENT_SECRET'],
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [CONFIG['OAUTH_REDIRECT_URI']]
        }
    }

    flow = Flow.from_client_config(
        client_config_dict,
        scopes=['openid', 'https://www.googleapis.com/auth/userinfo.email',
                'https://www.googleapis.com/auth/userinfo.profile']
    )
    flow.redirect_uri = CONFIG['OAUTH_REDIRECT_URI']
    return flow


# --- Routes ---
@app.route("/")
def home():
    return redirect(url_for('gn_ticket_page')) if 'user' in session else render_template("home.html")


@app.route("/login")
def login():
    flow = create_flow()
    if not flow:
        return render_template("oauth_not_configured.html")

    try:
        auth_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
        session['oauth_state'] = state
        # google-auth-oauthlib generates a PKCE verifier inside authorization_url() and
        # keeps it on this Flow object. The callback builds a *different* Flow, so the
        # verifier has to travel through the session or Google rejects the exchange with
        # "Missing code verifier".
        session['oauth_code_verifier'] = flow.code_verifier
        return redirect(auth_url)
    except Exception as e:
        logging.error(f"OAuth setup error: {e}", exc_info=True)
        return redirect(url_for('login_error'))


@app.route("/oauth/callback")
def oauth_callback():
    """Handle OAuth callback"""
    if 'code' not in request.args:
        logging.error("OAuth callback missing authorization code")
        return redirect(url_for('login_error'))

    # Guards against a callback being replayed from somewhere else. A mismatch here
    # usually means the login URL was opened in a different browser than it was
    # generated in, which cannot work: the PKCE verifier lives in that browser's session.
    expected_state = session.get('oauth_state')
    if not expected_state or request.args.get('state') != expected_state:
        logging.error("OAuth state mismatch — open the login link in the browser that generated it.")
        return redirect(url_for('login_error'))

    flow = create_flow()
    if not flow:
        return render_template("oauth_not_configured.html")

    # Restore the PKCE verifier this browser started the flow with (see /login).
    flow.code_verifier = session.get('oauth_code_verifier')

    try:
        flow.fetch_token(authorization_response=request.url)
        # One-time values; they must not be reusable after a successful exchange.
        session.pop('oauth_state', None)
        session.pop('oauth_code_verifier', None)

        # The id_token arrived over TLS straight from Google's token endpoint in
        # exchange for our client secret, so its signature is not re-verified here.
        token_payload = jwt.decode(flow.credentials.id_token, options={"verify_signature": False})
        user_info = {
            'email': token_payload.get('email'),
            'name': token_payload.get('name'),
            'picture': token_payload.get('picture', ''),
            'id': token_payload.get('sub')
        }

        domain = user_info['email'].split('@')[-1].lower()
        if not any(domain == allowed.strip() for allowed in CONFIG['ALLOWED_DOMAINS']):
            return render_template("access_denied.html", email=user_info['email'],
                                   allowed_domains=CONFIG['ALLOWED_DOMAINS'])

        user_manager.upsert_user(user_info['email'], user_info.get('name'), user_info.get('picture', ''))
        session['user'] = user_info

        if user_manager.is_profile_complete(user_info['email']) is False:
            return redirect(url_for('setup_profile'))
        return redirect(url_for('gn_ticket_page'))

    except Exception as e:
        logging.error(f"OAuth callback error: {e}", exc_info=True)
        return redirect(url_for('login_error'))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))


@app.route("/login-error")
def login_error():
    return render_template("login_error.html")


def _latest_scan(db_user, db):
    """The most recent scan's conflicts and summary, for the dashboard."""
    scan = db.execute(
        select(ScanResult)
        .where(ScanResult.user_id == db_user.id)
        .order_by(ScanResult.scanned_at.desc())
    ).scalars().first()
    if not scan:
        return [], None

    try:
        conflicts = json.loads(scan.conflicts_json) if scan.conflicts_json else []
    except (TypeError, json.JSONDecodeError):
        conflicts = []
    try:
        summary = json.loads(scan.summary) if scan.summary else {}
    except (TypeError, json.JSONDecodeError):
        summary = {}
    summary['scanned_at'] = scan.scanned_at.isoformat() if scan.scanned_at else None
    return conflicts, summary


@app.route("/gn_ticket", methods=["GET", "POST"])
@require_auth
def gn_ticket_page():
    user = session.get('user', {})
    if not user_manager.is_profile_complete(user['email']):
        return redirect(url_for('setup_profile'))

    profile = user_manager.load_profile(user['email'])
    if not profile:
        return redirect(url_for('setup_profile'))

    # Credentials that no longer decrypt (a rotated APP_ENCRYPTION_KEY) come back
    # as noise; a real Airtable key always starts with "pat".
    airtable_key = profile.get('airtable_api_key', '')
    if airtable_key and not airtable_key.startswith('pat'):
        migration_error = ("Your saved credentials could not be decrypted, likely due to a recent app update. "
                           "Please re-enter them one time to continue.")
        return render_template("setup_profile.html", user=user, profile={}, error=migration_error)

    try:
        prefs = profile.get('preferences', {})
        buffer_before = prefs.get('buffer_before', 10)
        buffer_after = prefs.get('buffer_after', 10)
        window_past_days = prefs.get('window_past_days', 14)
        window_future_days = prefs.get('window_future_days', 90)
        auto_booking_enabled = prefs.get('auto_booking_enabled', False)
        scan_frequency_hours = prefs.get('scan_frequency_hours', 24)
        notification_email = prefs.get('notification_email', '')

        # The look-ahead slider narrows the visible sessions in the browser without a
        # round trip. Widening it needs sessions this page never loaded, so the slider
        # reloads with ?future_days=N and the fresh window is pulled from Airtable
        # below. Saving it keeps the scheduled run looking at the same horizon.
        requested_future_days = request.args.get('future_days')
        if requested_future_days is not None:
            window_future_days = normalize_lookahead(requested_future_days)
            if window_future_days != prefs.get('window_future_days'):
                user_manager.update_preferences(user['email'],
                                                {'window_future_days': window_future_days})

        lookahead_index = next(
            (i for i, (days, _) in enumerate(LOOKAHEAD_STOPS) if days == window_future_days), 0)
        lookahead_label = LOOKAHEAD_STOPS[lookahead_index][1]

        airtable_client = create_airtable_client(profile['airtable_api_key'])
        candidate_sessions = airtable_client.get_booked_sessions(
            user_email=user['email'],
            window_past_days=window_past_days,
            window_future_days=window_future_days,
        )
        # The same check the scheduled run applies, so the page shows what the run
        # would do.
        candidate_sessions = tasks.annotate_conflicts(
            airtable_client, candidate_sessions, user['email'], window_past_days, window_future_days)

        submitted_ticket_log = ticket_log.get_entries(user['email'], window_past_days=window_past_days)
        submitted_ticket_log = sorted(submitted_ticket_log, key=lambda entry: entry.get('submitted_at', ''), reverse=True)

        # Sessions taken off the list previously. They stay off it until put back.
        excluded_ids = tasks.excluded_session_ids(user['email'])

        session['book_session_ids'] = [s.s_id for s in candidate_sessions]

        latest_conflicts, latest_scan, emailed_conflict_ids = [], None, set()
        with SessionLocal() as db:
            db_user = db.execute(
                select(User).where(User.email == user['email'].strip().lower())
            ).scalar_one_or_none()
            if db_user:
                latest_conflicts, latest_scan = _latest_scan(db_user, db)
                emailed_conflict_ids = {
                    row.session_id for row in db.execute(
                        select(ConflictEmailLog).where(ConflictEmailLog.user_id == db_user.id)
                    ).scalars().all()
                }

        return render_template(
            "gn.html",
            all_sessions=candidate_sessions,
            submitted_ticket_log=submitted_ticket_log,
            latest_conflicts=latest_conflicts,
            emailed_conflict_ids=emailed_conflict_ids,
            excluded_ids=excluded_ids,
            user=user,
            buffer_before=buffer_before,
            buffer_after=buffer_after,
            window_past_days=window_past_days,
            window_future_days=window_future_days,
            lookahead_stops=LOOKAHEAD_STOPS,
            lookahead_forever_days=LOOKAHEAD_FOREVER_DAYS,
            lookahead_index=lookahead_index,
            lookahead_label=lookahead_label,
            auto_booking_enabled=auto_booking_enabled,
            scan_frequency_hours=scan_frequency_hours,
            scan_frequency_choices=SCAN_FREQUENCY_CHOICES,
            notification_email=notification_email,
            account_email=user['email'],
            manual_booking_enabled=MANUAL_BOOKING_ENABLED,
            scan_notice=session.pop('scan_notice', None),
            auto_scan_time=auto_scan_time_label(scan_frequency_hours),
            latest_scan=latest_scan,
            booking_busy_since=booking_in_progress(),
        )
    except Exception as e:
        logging.error(f"Error loading sessions: {e}", exc_info=True)
        return render_template("profile_error.html", error=str(e), user=user)


@app.route("/gn_ticket/book_sessions", methods=["POST"])
@require_auth
def do_gn_ticket():
    user = session['user']
    if not MANUAL_BOOKING_ENABLED:
        logging.info("Manual booking is disabled here; refusing the request.")
        return render_template("manual_booking_disabled.html", user=user), 403

    # Buffers are edited on this form; they are a preference, so they stick.
    buffer_before = int(request.form.get('buffer_before', 10) or 0)
    buffer_after = int(request.form.get('buffer_after', 10) or 0)
    user_manager.update_preferences(user['email'],
                                    {'buffer_before': buffer_before, 'buffer_after': buffer_after})

    profile = user_manager.load_profile(user['email'])
    prefs = profile.get('preferences', {})

    selected_ids = set(get_enabled_sessions(request))
    candidate_ids = set(session.get('book_session_ids', []))
    effective_ids = selected_ids.intersection(candidate_ids) if candidate_ids else selected_ids
    # A removed session is never booked, whatever the form happens to say.
    effective_ids -= tasks.excluded_session_ids(user['email'])

    airtable_client = create_airtable_client(profile['airtable_api_key'])
    candidate_sessions = airtable_client.get_booked_sessions(
        user_email=user['email'],
        window_past_days=prefs.get('window_past_days', 14),
        window_future_days=prefs.get('window_future_days', 90),
    )
    send_to_gn = [s for s in candidate_sessions if s.s_id in effective_ids]

    progress_session_id = f"gn_booking_{int(time.time())}_{secrets.token_hex(4)}"
    start_progress(progress_session_id, user['email'])
    set_progress(progress_session_id, f"Starting booking process for {len(send_to_gn)} sessions...")
    headless_mode_enabled = (request.form.get('watch_browser') != 'yes')

    def run_booking():
        try:
            def announce(seconds_left):
                set_progress(progress_session_id, busy_notice(seconds_left), status="waiting")

            outcome = tasks.submit_to_gn(
                user['email'], profile, send_to_gn,
                progress_session_id=progress_session_id,
                headless_mode=headless_mode_enabled,
                allow_manual_site_selection=True,
                on_wait=announce,
            )
            if outcome is None:
                set_progress(
                    progress_session_id,
                    "Another booking run is still going after a long wait. Nothing was "
                    "booked — these sessions are untouched, so try again shortly.",
                    status="error",
                )
                return
            successful, failed = outcome
            notify_booking_finished(user['email'], successful, failed)
        except Exception as e:
            set_progress(progress_session_id, f"Critical error during booking: {str(e)}", status="error")
            logging.error(f"Booking thread failed: {e}", exc_info=True)
            notify_booking_finished(
                user['email'], [],
                [{'title': f"{len(send_to_gn)} session(s)", 'error': str(e)}],
            )

    threading.Thread(target=run_booking, daemon=True).start()

    sessions_for_template = [
        {
            's_id': s.s_id,
            'title': s.title,
            'school': s.school,
            'community': s.community,
            'start_time_iso': s.start_time.isoformat() if s.start_time else '',
            'length': s.length,
        }
        for s in send_to_gn
    ]

    return render_template(
        "progress.html",
        progress_session_id=progress_session_id,
        session_count=len(send_to_gn),
        sessions=sessions_for_template,
        submitted_ticket_log=ticket_log.get_entries(user['email']),
        user=user,
    )


@app.route("/gn_ticket/conflict_emailed", methods=["POST"])
@require_auth
def record_conflict_email():
    """Record that a conflict notification email was sent for a session."""
    user = session['user']
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id', '').strip()
    conflict_session_id = data.get('conflict_session_id', '').strip()
    if not session_id:
        return jsonify({'ok': False, 'error': 'missing session_id'}), 400
    try:
        with SessionLocal() as db:
            db_user = db.execute(
                select(User).where(User.email == user['email'].strip().lower())
            ).scalar_one_or_none()
            if not db_user:
                return jsonify({'ok': False, 'error': 'user not found'}), 404
            # Upsert: delete existing record for this session_id then insert fresh
            db.execute(sa_delete(ConflictEmailLog).where(
                ConflictEmailLog.user_id == db_user.id,
                ConflictEmailLog.session_id == session_id,
            ))
            db.add(ConflictEmailLog(
                user_id=db_user.id,
                session_id=session_id,
                conflict_session_id=conflict_session_id,
            ))
            db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        logging.error(f"conflict_emailed error: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route("/gn_ticket/conflict_resolved", methods=["POST"])
@require_auth
def resolve_conflict_route():
    """Settle a clash: this class joins by Zoom, the other keeps the Cisco machine.

    Two things have to happen together. The host needs to know to expect Zoom, so
    the note goes on the Airtable record they read before the session; and the scan
    has to stop holding the session back, or the same overlap is found again in an
    hour. The note is written first — if Airtable refuses, nothing is recorded and
    the button stays as it was.
    """
    user = session['user']
    data = request.get_json(silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    conflict_session_id = (data.get('conflict_session_id') or '').strip()
    if not session_id:
        return jsonify({'ok': False, 'error': 'missing session_id'}), 400

    profile = user_manager.load_profile(user['email'])
    if not profile or not profile.get('airtable_api_key'):
        return jsonify({'ok': False, 'error': 'Airtable is not set up for this account'}), 400

    try:
        airtable_client = create_airtable_client(profile['airtable_api_key'])
        airtable_client.append_session_note(session_id, tasks.HOST_NOTES_FIELD,
                                            tasks.ZOOM_RESOLUTION_NOTE)
    except Exception as e:
        logging.error(f"conflict_resolved note failed: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': f'Could not write the host note: {e}'}), 502

    try:
        if not tasks.record_conflict_resolution(user['email'], session_id, conflict_session_id):
            return jsonify({'ok': False, 'error': 'user not found'}), 404
    except Exception as e:
        logging.error(f"conflict_resolved error: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

    return jsonify({'ok': True, 'note': tasks.ZOOM_RESOLUTION_NOTE})


@app.route("/gn_ticket/set_excluded", methods=["POST"])
@require_auth
def set_session_excluded_route():
    """Remove a session from booking, or put it back. Sticks across future runs."""
    user = session['user']
    data = request.get_json(silent=True) or {}
    session_id = (data.get('session_id') or '').strip()
    if not session_id:
        return jsonify({'ok': False, 'error': 'missing session_id'}), 400

    excluded = bool(data.get('excluded'))
    start_time = None
    raw_start = (data.get('start_time') or '').strip()
    if raw_start:
        try:
            parsed = datetime.fromisoformat(raw_start.replace('Z', '+00:00'))
            # Stored naive UTC, matching every other timestamp in the database.
            start_time = (parsed.astimezone(timezone.utc).replace(tzinfo=None)
                          if parsed.tzinfo else parsed)
        except ValueError:
            logging.info("Ignoring unparseable start_time %r for %s", raw_start, session_id)

    try:
        ok = tasks.set_session_excluded(
            user['email'], session_id, excluded,
            title=(data.get('title') or '').strip() or None,
            school=(data.get('school') or '').strip() or None,
            start_time=start_time,
        )
        if not ok:
            return jsonify({'ok': False, 'error': 'user not found'}), 404
        return jsonify({'ok': True, 'excluded': excluded})
    except Exception as e:
        logging.error(f"set_excluded error: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route("/gn_ticket/set_lookahead", methods=["POST"])
@require_auth
def set_lookahead_route():
    """Save the look-ahead slider. It is a preference, not a view setting: the
    scheduled run reads the same window, so moving the slider also decides how far
    ahead auto-booking reaches."""
    user = session['user']
    data = request.get_json(silent=True) or {}
    if 'window_future_days' not in data:
        return jsonify({'ok': False, 'error': 'missing window_future_days'}), 400

    window_future_days = normalize_lookahead(data.get('window_future_days'))
    try:
        user_manager.update_preferences(user['email'],
                                        {'window_future_days': window_future_days})
    except Exception as e:
        logging.error(f"set_lookahead error: {e}", exc_info=True)
        return jsonify({'ok': False, 'error': str(e)}), 500

    label = next((lbl for days, lbl in LOOKAHEAD_STOPS if days == window_future_days),
                 str(window_future_days))
    return jsonify({'ok': True, 'window_future_days': window_future_days, 'label': label})


@app.route("/progress/<session_id>")
@require_auth
def stream_session_progress(session_id):
    """Stream progress updates for a given session ID using Server-Sent Events."""
    owner = session['user']['email']
    if get_progress(session_id, owner) is None:
        abort(404)

    def generate():
        last_seq = 0
        # Bounded: a stream that never sees a terminal entry (the run died before
        # writing one) must not hold this worker's thread forever.
        deadline = time.monotonic() + PROGRESS_KEEP_SECONDS
        while time.monotonic() < deadline:
            progress = get_progress(session_id, owner) or []
            for entry in progress:
                if entry.get('seq', 0) > last_seq:
                    yield f"data: {json.dumps(entry)}\n\n"
                    last_seq = entry['seq']
                    if entry.get("status") in ("completed", "error"):
                        return
            time.sleep(1)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/progress-status/<session_id>")
@require_auth
def get_session_progress_status(session_id):
    """Return progress updates, optionally filtered by a last sequence number."""
    last_seq = request.args.get('lastSeq', type=int)
    progress = get_progress(session_id, session['user']['email'])
    if progress is None:
        abort(404)

    if not progress:
        return jsonify({'entries': [], 'reset': bool(last_seq)})

    earliest = progress[0]['seq']
    latest = progress[-1]['seq']

    reset = False
    if last_seq is not None:
        if last_seq < earliest - 1 or last_seq > latest:
            reset = True

    if reset or last_seq is None:
        return jsonify({'entries': progress, 'reset': reset or last_seq is None})

    new_entries = [e for e in progress if e['seq'] > last_seq]
    return jsonify({'entries': new_entries, 'reset': False})


@app.route("/setup", methods=["GET", "POST"])
@require_auth
def setup_profile():
    user = session['user']
    if request.method == 'POST':
        profile_data = {
            'airtable_api_key': request.form.get('airtable_api_key', '').strip(),
            'servicenow_password': request.form.get('servicenow_password', '').strip(),
            'totp_secret': request.form.get('totp_secret', '').strip().replace(' ', '')
        }
        required_fields = ['airtable_api_key', 'servicenow_password', 'totp_secret']
        if not all(profile_data[field] for field in required_fields):
            return render_template("setup_profile.html", user=user, profile=profile_data,
                                   error="Airtable API key, ServiceNow password, and TOTP secret are required.")
        if not profile_data['airtable_api_key'].startswith('pat'):
            return render_template("setup_profile.html", user=user, profile=profile_data,
                                   error="Airtable API key must start with 'pat'.")

        try:
            user_manager.upsert_user(user['email'], user.get('name'), user.get('picture', ''))
            user_manager.save_profile(user['email'], profile_data)
            return redirect(url_for('gn_ticket_page'))
        except Exception as e:
            return render_template("setup_profile.html", user=user, profile=profile_data,
                                   error=f"Error saving profile: {e}")

    existing_profile = user_manager.load_profile(user['email']) or {}
    error = request.args.get('error')
    return render_template("setup_profile.html", user=user, profile=existing_profile, error=error)


@app.route("/import-desktop", methods=["GET", "POST"])
@require_auth
def import_desktop():
    """Let someone moving off the desktop app bring their saved setup with them."""
    user = session['user']
    has_profile = user_manager.is_profile_complete(user['email'])
    if request.method == 'GET':
        return render_template("import_desktop.html", user=user, has_profile=has_profile)

    # A folder drop arrives as many files; the import picks out the database itself,
    # so nobody has to know which one it is.
    uploads = [f for f in request.files.getlist('desktop_files') if f and f.filename]
    if not uploads:
        return render_template("import_desktop.html", user=user, has_profile=has_profile,
                               error="Drag in your GN_Ticket_Automator folder first.")

    import shutil
    import tempfile
    workspace = tempfile.mkdtemp(prefix="desktop_import_")
    try:
        paths = []
        for index, upload in enumerate(uploads[:MAX_UPLOAD_FILES]):
            path = os.path.join(workspace, str(index))
            with open(path, "wb") as out:
                size = 0
                for chunk in iter(lambda: upload.stream.read(64 * 1024), b""):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        break  # far too big to be anything we want; skip the rest of it
                    out.write(chunk)
            paths.append(path)

        result = import_desktop_files(paths, user['email'], name=user.get('name'),
                                      replace=request.form.get('replace') == 'yes')
    except DesktopImportError as exc:
        return render_template("import_desktop.html", user=user, has_profile=has_profile, error=str(exc))
    except Exception:
        logging.exception("Desktop import failed for %s", user['email'])
        return render_template("import_desktop.html", user=user, has_profile=has_profile,
                               error="Something went wrong reading those files. Enter your details in the setup steps instead.")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    logging.info("Imported desktop profile for %s: %s", user['email'], result)
    return render_template("import_desktop.html", user=user, has_profile=True, result=result)


@app.route("/preferences", methods=["POST"])
@require_auth
def update_preferences():
    user = session['user']
    prefs = {
        "buffer_before": int(request.form.get("buffer_before", 10) or 0),
        "buffer_after": int(request.form.get("buffer_after", 10) or 0),
        "window_past_days": int(request.form.get("window_past_days", 14) or 0),
        "auto_booking_enabled": request.form.get("auto_booking_enabled") == "yes",
        "scan_frequency_hours": normalize_scan_frequency(request.form.get("scan_frequency_hours")),
        "notification_email": request.form.get("notification_email"),
    }
    # The look-ahead lives on the slider, not in this form. Only write it when it was
    # actually submitted, or saving settings would snap the slider back to the default.
    if "window_future_days" in request.form:
        prefs["window_future_days"] = normalize_lookahead(request.form.get("window_future_days"))
    user_manager.update_preferences(user['email'], prefs)
    return redirect(url_for('gn_ticket_page'))


@app.route("/auto/run", methods=["POST"])
@require_auth
def run_auto_scan_now():
    user = session['user']

    # Record the ask first, so it survives whatever happens next: if the trigger
    # fails, or is not configured at all, the next scheduled run still honours it
    # regardless of this user's interval.
    tasks.request_scan(user['email'])

    if MANUAL_BOOKING_ENABLED:
        # This process has the memory for a browser, so do it here.
        threading.Thread(target=dispatch_scan, args=(user['email'],), daemon=True).start()
        session['scan_notice'] = ("The booker is running. You will get an email at "
                                  f"{user['email']} when it finishes, with anything booked, "
                                  "skipped for a conflict, or failed.")
        return redirect(url_for('gn_ticket_page'))

    # Hosted: the scan job has the memory for Chrome, this process does not.
    # Triggering cancels any run already going, which could kill a booking partway
    # through and lose its local record, so defer to one already in progress.
    busy_since = booking_in_progress()
    if busy_since:
        session['scan_notice'] = ("A booking run is already in progress — your request is "
                                  "queued and will be picked up by it or the next check.")
        return redirect(url_for('gn_ticket_page'))

    ok, message = render_api.trigger_scan_job()
    if ok:
        message = (f"{message} You will get an email at {user['email']} when it finishes, "
                   "with anything booked, skipped for a conflict, or failed.")
    session['scan_notice'] = message
    if not ok:
        logging.info("On-demand scan for %s fell back to the schedule.", user['email'])
    return redirect(url_for('gn_ticket_page'))


def get_enabled_sessions(request):
    """Correctly extract selected session IDs from the form."""
    airtable_ids = request.form.getlist('airtable_id')
    book_me_values = request.form.getlist('book_me')
    enabled_sessions = []
    for i, airtable_id in enumerate(airtable_ids):
        if i < len(book_me_values) and book_me_values[i] == 'y':
            enabled_sessions.append(airtable_id)
    return enabled_sessions


gn_ticket.set_progress_callback(set_progress)

if __name__ == "__main__":
    app.run(debug=False, port=int(os.getenv("PORT", "5001")))
