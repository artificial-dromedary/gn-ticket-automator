from flask import Flask, session, Response, jsonify, redirect, url_for
from flask import render_template
from flask import request
import json
import time
import threading
from datetime import datetime
import os
from dotenv import load_dotenv
import secrets
import sys
from pathlib import Path
import logging
import jwt

# Optional environment diagnostics, enabled via ENABLE_ENV_DIAGNOSTICS
if os.environ.get("ENABLE_ENV_DIAGNOSTICS", "").lower() in ("1", "true", "yes"):
    import google_auth_oauthlib
    import inspect

    print("--- PYTHON ENVIRONMENT DIAGNOSTICS ---")
    try:
        version = getattr(google_auth_oauthlib, "__version__", "Version not available")
        print(f"google_auth_oauthlib version: {version}")
        print(f"Module loaded from: {google_auth_oauthlib.__file__}")
        print(f"Flow module location: {inspect.getfile(google_auth_oauthlib.flow)}")
        print(
            f"Available attributes: {[attr for attr in dir(google_auth_oauthlib) if not attr.startswith('_')]}")
    except Exception as e:
        print(f"Could not print diagnostics, error: {e}")
    print("--------------------------------------")


def load_env_file():
    """Load .env file from the appropriate location for both development and bundled app"""
    if getattr(sys, 'frozen', False):
        # Running as bundled app - check bundle first, then user directory
        possible_locations = [
            Path(sys._MEIPASS) / '.env' if hasattr(sys, '_MEIPASS') else None,
            Path(os.path.dirname(sys.executable)) / '.env',
            Path.home() / 'GN_Ticket_Automator' / '.env',
        ]

        for env_path in possible_locations:
            if env_path and env_path.exists():
                load_dotenv(env_path)
                return True
        return False
    else:
        load_dotenv()
        return True


env_loaded = load_env_file()


def get_app_data_dir():
    """Get the application data directory for user data"""
    app_dir = Path.home() / 'GN_Ticket_Automator' if getattr(sys, 'frozen', False) else Path(__file__).parent
    app_dir.mkdir(exist_ok=True)
    return app_dir


# Configure paths for bundled app
template_dir, static_dir = None, None
if getattr(sys, 'frozen', False):
    base_path = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(os.path.dirname(sys.executable))
    resources_path = base_path.parent / 'Resources'  # macOS app bundle structure

    # Check MEIPASS/executable path first, then Resources path
    t_dir = base_path / 'templates'
    s_dir = base_path / 'static'

    if t_dir.exists():
        template_dir = str(t_dir)
    elif (resources_path / 'templates').exists():
        template_dir = str(resources_path / 'templates')

    if s_dir.exists():
        static_dir = str(s_dir)
    elif (resources_path / 'static').exists():
        static_dir = str(resources_path / 'static')

# Create Flask app with proper directories
if template_dir and static_dir:
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    logging.info(f"✅ Flask app created with custom template/static directories")
else:
    app = Flask(__name__)
    logging.warning(f"⚠️ Flask app created with default directories")

# Allow insecure transport for development (localhost only)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from flask_session import Session
import gn_ticket
from user_profiles import user_manager
from airtable_integration import create_airtable_client
from updater import AppUpdater, APP_VERSION
from google_auth_oauthlib.flow import InstalledAppFlow

# Global progress storage
progress_store = {}
progress_lock = threading.Lock()


def load_config_from_env():
    config = {
        'GOOGLE_CLIENT_ID': os.getenv('GOOGLE_CLIENT_ID'),
        'GOOGLE_CLIENT_SECRET': os.getenv('GOOGLE_CLIENT_SECRET'),
        'OAUTH_REDIRECT_URI': os.getenv('OAUTH_REDIRECT_URI', 'http://127.0.0.1:5000/oauth/callback'),
        'ALLOWED_DOMAINS': os.getenv('ALLOWED_DOMAINS', 'takingitglobal.org').split(','),
        'SECRET_KEY': os.getenv('SECRET_KEY'),
        'CHATGPT_API_KEY': os.getenv('CHATGPT_API_KEY')
    }
    is_valid = bool(config['GOOGLE_CLIENT_ID'] and config['GOOGLE_CLIENT_SECRET'])
    if not is_valid:
        logging.error("Missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET environment variable.")
        print(f"*** DEBUG: Loaded CONFIG = {CONFIG}")
    return config, is_valid


if not env_loaded:
    logging.error("Failed to load .env file. Please ensure it exists and is accessible.")

# Session configuration
app.config['SESSION_TYPE'] = 'filesystem'
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
Session(app)

CONFIG, CONFIG_VALID = load_config_from_env()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def get_redirect_uri_for_flow():
    """Determine the redirect URI to use for the OAuth flow, prioritizing the configured Flask route."""
    return CONFIG['OAUTH_REDIRECT_URI']


def create_flow():
    """Create Google OAuth flow for desktop application"""
    if not CONFIG_VALID:
        return None

    # For installed/desktop apps, use this configuration
    client_config_dict = {
        "installed": {
            "client_id": CONFIG['GOOGLE_CLIENT_ID'],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": CONFIG['GOOGLE_CLIENT_SECRET'],  # Required even for desktop apps
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://127.0.0.1:5000/oauth/callback", "urn:ietf:wg:oauth:2.0:oob"]
        }
    }

    # Use InstalledAppFlow for desktop applications
    flow = InstalledAppFlow.from_client_config(
        client_config_dict,
        scopes=['openid', 'https://www.googleapis.com/auth/userinfo.email',
                'https://www.googleapis.com/auth/userinfo.profile']
    )
    return flow


def set_progress(session_id, message, step=None, total_steps=None, status="running"):
    """Update progress for a specific session"""
    with progress_lock:
        if session_id not in progress_store:
            progress_store[session_id] = []
        progress_store[session_id].append({
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'message': message, 'step': step, 'total_steps': total_steps, 'status': status
        })
        progress_store[session_id] = progress_store[session_id][-50:]


def get_progress(session_id):
    with progress_lock:
        return progress_store.get(session_id, [])


def clear_progress(session_id):
    with progress_lock:
        progress_store.pop(session_id, None)


def require_auth(f):
    """Decorator to require authentication"""

    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    decorated_function.__name__ = f.__name__
    return decorated_function


# --- Routes ---
@app.route("/")
def home():
    return redirect(url_for('gn_ticket_page')) if 'user' in session else render_template("home.html")


@app.route("/login")
def login():
    flow = create_flow()
    if not flow:
        return render_template("oauth_not_configured.html")

    # Generate authorization URL and show it to user
    try:
        flow.redirect_uri = get_redirect_uri_for_flow()  # Ensure consistency for generating the auth URL
        auth_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
        session['oauth_state'] = state

        return render_template("manual_oauth.html", auth_url=auth_url)

    except Exception as e:
        logging.error(f"OAuth setup error: {e}", exc_info=True)
        return redirect(url_for('login_error'))


@app.route("/oauth/callback")
def oauth_callback():
    """Handle OAuth callback"""
    if 'code' not in request.args:
        logging.error("OAuth callback missing authorization code")
        return redirect(url_for('login_error'))

    logging.info(f"OAuth callback received with code. Request URL: {request.url}")

    # Create a new flow for the callback
    flow = create_flow()
    if not flow:
        return render_template("oauth_not_configured.html")

    flow.redirect_uri = get_redirect_uri_for_flow()

    try:
        # Use the authorization code directly for installed apps
        flow.fetch_token(code=request.args.get('code'))

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

        session['user'] = user_info

        if user_manager.is_profile_complete(user_info['email']) is False:
            return redirect(url_for('setup_profile'))
        else:
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


@app.route("/gn_ticket", methods=["GET", "POST"])
@require_auth
def gn_ticket_page():
    user = session.get('user', {})
    if not user_manager.is_profile_complete(user['email']):
        return redirect(url_for('setup_profile'))

    profile = user_manager.load_profile(user['email'])
    if not profile:
        return redirect(url_for('setup_profile'))

    # --- DECRYPTION FAILURE CHECK ---
    airtable_key = profile.get('airtable_api_key', '')
    if airtable_key and not airtable_key.startswith('pat'):
        migration_error = ("Your saved credentials could not be decrypted, likely due to a recent app update. "
                           "Please re-enter them one time to continue.")
        return render_template("setup_profile.html", user=user, profile={}, error=migration_error)
    # --- END CHECK ---

    if 'update_checked' not in session:
        session['update_checked'] = True
        try:
            logging.info("Checking for updates on startup...")
            update_info = AppUpdater().check_for_updates()
            if update_info.get('available'):
                session['update_info'] = update_info
                session['current_version'] = AppUpdater().current_version
                return render_template("update.html", update_info=update_info,
                                       current_version=session['current_version'], user=user, auto_detected=True)
            else:
                logging.info("App is up to date.")
        except Exception as e:
            logging.error(f"Startup update check failed: {e}", exc_info=True)

    if session.get('update_info') and not session.get('update_dismissed'):
        return render_template("update.html", update_info=session['update_info'],
                               current_version=session.get('current_version'), user=user, auto_detected=True)

    try:
        sessions_data = create_airtable_client(profile['airtable_api_key']).get_booked_sessions(user_name=user['name'])
        session['book_sessions'] = sessions_data
        return render_template("gn.html", all_sessions=sessions_data, user=user)
    except Exception as e:
        logging.error(f"Error loading sessions: {e}", exc_info=True)
        return render_template("profile_error.html", error=str(e), user=user)


@app.route("/gn_ticket/book_sessions", methods=["POST"])
@require_auth
def do_gn_ticket():
    user = session['user']
    profile = user_manager.load_profile(user['email'])

    send_to_gn = [s for s in session.get('book_sessions', []) if s.s_id in get_enabled_sessions(request)]

    progress_session_id = f"gn_booking_{int(time.time())}"
    clear_progress(progress_session_id)
    set_progress(progress_session_id, f"Starting booking process for {len(send_to_gn)} sessions...")

    # Extract necessary data from the request *before* spawning the thread
    headless_mode_enabled = (request.form.get('watch_browser') != 'yes')

    def run_booking():
        try:
            gn_ticket.gn_ticket_handler(
                send_to_gn,
                user['email'],
                profile.get('servicenow_password'),
                "connectednorth@takingitglobal.org",
                progress_session_id,
                profile.get('airtable_api_key'),
                profile.get('totp_secret'),
                headless_mode=headless_mode_enabled,  # This will control browser visibility
                allow_manual_site_selection=True,  # Explicitly enable manual intervention
                chatgpt_api_key=CONFIG.get('CHATGPT_API_KEY')
            )
        except Exception as e:
            set_progress(progress_session_id, f"Critical error during booking: {str(e)}", status="error")
            logging.error(f"Booking thread failed: {e}", exc_info=True)

    threading.Thread(target=run_booking, daemon=True).start()

    return render_template("progress.html", progress_session_id=progress_session_id,
                           session_count=len(send_to_gn), user=user)


@app.route("/progress/<session_id>")
@require_auth
def stream_session_progress(session_id):
    """Stream progress updates for a given session ID using Server-Sent Events."""

    def generate():
        last_index = 0
        while True:
            progress = get_progress(session_id)
            while last_index < len(progress):
                entry = progress[last_index]
                yield f"data: {json.dumps(entry)}\n\n"
                last_index += 1
                if entry.get("status") in ("completed", "error"):
                    return
            time.sleep(1)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/progress-status/<session_id>")
@require_auth
def get_session_progress_status(session_id):
    """Return the full progress history for manual refresh."""
    return jsonify(get_progress(session_id))


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
            user_manager.save_profile(user['email'], profile_data)
            return redirect(url_for('gn_ticket_page'))
        except Exception as e:
            return render_template("setup_profile.html", user=user, profile=profile_data,
                                   error=f"Error saving profile: {e}")

    existing_profile = user_manager.load_profile(user['email']) or {}
    error = request.args.get('error')
    return render_template("setup_profile.html", user=user, profile=existing_profile, error=error)


@app.route("/update/check")
@require_auth
def check_updates():
    try:
        update_info = AppUpdater().check_for_updates()
        return render_template("update.html", update_info=update_info, current_version=AppUpdater().current_version,
                               user=session['user'])
    except Exception as e:
        return f"<h1>Update Check Error</h1><p style='color:red;'>{e}</p>"


@app.route("/update/install", methods=["POST"])
@require_auth
def install_update():
    try:
        update_info = AppUpdater().check_for_updates()
        if not update_info.get('available'):
            return jsonify({"success": False, "error": "No update available"})

        def run_update():
            try:
                AppUpdater().prepare_and_launch_installer(update_info['download_url'])
            except Exception as e:
                logging.error(f"Update preparation failed: {e}", exc_info=True)

        threading.Thread(target=run_update, daemon=True).start()

        return """<html><body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
                    <h1>Update in Progress</h1><p>A native progress window has opened.</p>
                    <p>You can close this browser window.</p></body></html>"""
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/update/dismiss", methods=["POST"])
@require_auth
def dismiss_update():
    session['update_dismissed'] = True
    session.pop('update_info', None)
    return jsonify({"success": True})


@app.route("/update/debug")
@require_auth
def debug_update_check():
    import requests, traceback
    from updater import GITHUB_REPO, UPDATE_CHECK_URL
    debug_info = {'current_version': APP_VERSION, 'github_repo': GITHUB_REPO, 'update_url': UPDATE_CHECK_URL}
    try:
        response = requests.get(UPDATE_CHECK_URL, timeout=10)
        debug_info['github_status_code'] = response.status_code
        debug_info['github_response'] = response.text
    except Exception as e:
        debug_info['error'] = str(e)
        debug_info['traceback'] = traceback.format_exc()
    return f"<pre>{json.dumps(debug_info, indent=2)}</pre>"


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
    app.run(debug=True)