# GN Ticket Automator

Files Government of Nunavut videoconference tickets for Connected North sessions,
so nobody has to type them into ServiceNow by hand. It reads booked sessions from
Airtable, checks each one for clashes, submits a ticket through the ServiceNow
portal with a browser, and writes the ticket number back to Airtable.

Two things run:

- **The web app** (`main.py`). Sign in with a TakingITGlobal Google account, save
  your Airtable key and ServiceNow credentials once, then see your upcoming
  sessions, what will be booked, and what is being held back and why.
- **The scheduled scan** (`run_scan.py`). An hourly cron job that scans everyone
  who opted in, books the conflict-free sessions, emails about conflicts, and
  sends an end-of-day summary.

Both run on Render from the same Docker image; see `render.yaml`, which is also
where every environment variable is documented.

The macOS desktop app that preceded this is no longer maintained. Its source is
at the git tag `desktop-app-final`. People still on it can bring their saved
setup across with the "Bring your settings across" link on the setup page.

## Running it locally

```sh
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt

# .env, or exported:
export APP_ENCRYPTION_KEY=$(python -c 'import secrets; print(secrets.token_hex(16))')
export GOOGLE_CLIENT_ID=...          # from the Google Cloud OAuth client
export GOOGLE_CLIENT_SECRET=...
export OAUTH_REDIRECT_URI=http://127.0.0.1:5001/oauth/callback
export DATABASE_URL=sqlite:///gn_ticket.db   # the default; Postgres in production

python main.py                       # http://127.0.0.1:5001
```

The scan, without a browser and without submitting anything:

```sh
python run_scan.py --dry-run --force          # everyone opted in, ignore intervals
python run_scan.py --user someone@takingitglobal.org --dry-run
python run_scan.py --list-users
python run_scan.py --daily-summary            # send the summary now
```

Chrome and chromedriver are needed for a real booking. Point `CHROME_BINARY` and
`CHROMEDRIVER` at them if they are not on the default path; the Dockerfile shows
what the hosted image installs.

## Tests and checks

```sh
python -m pytest -q
ruff check .
```

CI runs both on every push and pull request. Tests use a scratch SQLite file and
a throwaway encryption key, and never reach Airtable, ServiceNow, Google or an
SMTP relay: each is faked at the module boundary.

## How a scan works

For each opted-in user whose slot has come round (`tasks.user_is_due`):

1. Read their upcoming Nunavut sessions from Airtable that are booked and not
   yet ticketed, within their look-ahead window.
2. Annotate conflicts (`conflict.py`): starting too soon for the GN to act on,
   overlapping an already-ticketed session at the same school, overlapping a
   ticket this tool already filed, or two candidates clashing with each other.
   Clashes the person has settled on the dashboard ("this class joins by Zoom")
   are cleared; sessions they removed are held back.
3. Record the scan, email any conflicts not already reported, and book the rest.

Booking (`tasks.submit_to_gn`) takes the single browser slot, a database-backed
lock shared by every process, logs in to ServiceNow with the user's password and
TOTP secret, fills the request form for each session, and records the ticket
number in Airtable and in the local ticket history. The web app's "Book selected
sessions" button goes through the same function where in-process booking is
enabled (`GN_ENABLE_MANUAL_BOOKING`); on the hosted service it is off and "Run
scan now" starts the cron job instead.

## Layout

```
main.py                  Flask app: sign-in, dashboard, settings, manual booking
run_scan.py              Cron job entrypoint
tasks.py                 Scan, conflict handling, booking, locks, daily summary
conflict.py              Conflict detection between sessions
gn_ticket.py             Selenium automation of the ServiceNow form
airtable_integration.py  Airtable client (retries, timeouts, formula quoting)
emailer.py               Every email, and the display-timezone formatting
models.py / db.py        SQLAlchemy models, engine, and init_db()
user_profiles.py         Encrypted credentials and per-user preferences
desktop_import.py        Importing a profile from the old desktop app's database
render_api.py            "Run scan now" on the hosted service
site_list.py             Fallback list of ServiceNow site names
templates/, static/      The pages
tests/                   pytest suite
```

## Conventions worth knowing

- **Timestamps in the database are naive UTC**, written with `models.utcnow()`.
  Anything shown to a person goes through `emailer.friendly_datetime`, which
  renders in `DISPLAY_TZ`. Session times from Airtable are aware datetimes.
- **The schema is brought up to date by `db.init_db()`**, called once at startup
  by `main.py` and `run_scan.py`. Nothing touches the database on import.
- **Every Airtable call goes through `AirtableIntegration`**, which retries the
  transient failures and quotes values into formulas. Do not call `requests`
  against Airtable directly.
- **Credentials are Fernet-encrypted** with `APP_ENCRYPTION_KEY`. Rotating that
  key means every user re-enters their setup.
- **There are no CSRF tokens.** The session cookie is SameSite=Lax and every
  state-changing request is checked against the `Sec-Fetch-Site` and `Origin`
  headers; see `refuse_cross_site_writes` in `main.py`.
- **Dependencies are pinned in `requirements.lock`**, which the image installs.
  Edit `requirements.txt` and regenerate the lock as its header describes.
