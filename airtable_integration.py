import logging
import os
import re
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# The Connected North base. Overridable so a copy of the base (for a rehearsal, or
# a second programme) is a dashboard edit rather than a code change.
DEFAULT_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appP1kThwW9zVEpHr")
DEFAULT_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME", "Sessions")

# Airtable stops answering mid-read for a few seconds now and then. At 15 seconds
# one of those was enough to take down a whole scheduled scan, so the read gets
# longer to finish and, more to the point, a second chance.
#
# Connect and read are separate because they fail differently: an unreachable host
# should give up quickly, a slow one deserves to be waited out.
CONNECT_TIMEOUT = float(os.getenv("AIRTABLE_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("REQUESTS_TIMEOUT", "30"))
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

# Attempts after the first, so 3 means four tries with 0s, 2s and 4s between them.
RETRY_ATTEMPTS = int(os.getenv("AIRTABLE_RETRY_ATTEMPTS", "3"))


def _build_http_session():
    """A requests session that retries the failures worth retrying.

    A read timeout and Airtable's own 429/5xx answers are transient: the same
    request a moment later usually works, and the alternative is failing a scan
    over a blip. PATCH is retried alongside GET because every write here sets one
    named field to a fixed value, so repeating one cannot do more than the first
    attempt already did.

    Retries live on the adapter rather than in a loop of our own so that they
    cover every call site, including the pagination inside _list.
    """
    retry = Retry(
        total=RETRY_ATTEMPTS,
        connect=RETRY_ATTEMPTS,
        read=RETRY_ATTEMPTS,
        status=RETRY_ATTEMPTS,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "PATCH"}),
        respect_retry_after_header=True,
        # A spent status retry comes back as the response rather than an exception,
        # so raise_for_status() still reports it with the body the logs expect.
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def quote(value):
    """A string literal for an Airtable formula.

    Names are interpolated into filterByFormula inside single quotes, so a school
    called "St. John's" used to end the literal early and fail the whole query,
    which took the scan down with it. Airtable escapes a quote with a backslash.
    """
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def future_cutoff(window_future_days):
    """Airtable expression for the far edge of the look-ahead window.

    DATEADD(TODAY(), N, 'day') is midnight at the *start* of day N, so comparing
    against it stops at the end of day N-1. The look-ahead slider means "through
    the end of day N", which the dashboard's own filtering already assumes — most
    visibly at its first stop, "Today" (0 days), where the previous formula matched
    nothing at all instead of the rest of today.
    """
    return f"DATEADD(TODAY(), {int(window_future_days) + 1}, 'day')"


def _any_of(field, values):
    conditions = [f"{{{field}}} = {quote(value)}" for value in values]
    return conditions[0] if len(conditions) == 1 else f"OR({', '.join(conditions)})"


def parse_airtable_datetime(value):
    """An aware datetime from Airtable's ISO text, or None when there is none.

    Airtable writes "2026-03-04T18:00:00.000Z". A value with no offset is taken as
    UTC. Unparseable or missing is None: it used to be "now", which then tripped
    the last-minute check and reported a dateless session as starting within
    twelve hours.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Could not parse Airtable date %r", value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# A Zoom meeting id, from any link shape Zoom hands out. Joined to @zoomcrc.com
# for the Cisco bridge, so it has to be the bare digits and nothing after them.
_ZOOM_ID = re.compile(r"/j/(\d{9,11})|(?<!\d)(\d{9,11})(?!\d)")


def zoom_meeting_id(link):
    """The meeting id inside a Zoom link, or None."""
    match = _ZOOM_ID.search(str(link or ""))
    return (match.group(1) or match.group(2)) if match else None


def _text(value, default=""):
    """First value of a lookup field, or the value itself, or the default."""
    if isinstance(value, list):
        return value[0] if value else default
    return value or default


def _all(value):
    """Every value of a lookup, not just the first.

    A session can be booked by more than one teacher, and the email to the school
    has to name all of them. Fields that feed the GN ticket form use _text, which
    stays on one value.
    """
    if isinstance(value, list):
        values = value
    elif value:
        values = [value]
    else:
        values = []
    return [str(v).strip() for v in values if str(v).strip()]


def _int(value, default):
    try:
        return int(_text(value, default) or default)
    except (ValueError, TypeError):
        return default


class AirtableSession:
    """One row of the Sessions table, read into the fields this tool uses."""

    def __init__(self, record_data):
        self.s_id = record_data["id"]
        fields = record_data.get("fields", {})

        title_raw = (fields.get("Session Title Text") or
                     fields.get("Session Title Raw") or
                     fields.get("Subject/Curriculum") or
                     fields.get("Primary Subject Text") or
                     fields.get("Session Title") or
                     fields.get("Provider Session Name") or "")
        self.title = _text(title_raw, "")
        # A linked-record id is not a title. Fall back to the description.
        if not self.title or self.title.startswith("rec"):
            desc = _text(fields.get("Session Description", ""), "")
            if desc and not desc.startswith("rec"):
                self.title = desc[:50] + "..." if len(desc) > 50 else desc
            else:
                self.title = f"Session {self.s_id}"

        self.school = _text(fields.get("School Name Text", ""), "Unknown School")
        # The GN form takes one client name, so self.teacher is the first; the
        # email to the school names every teacher.
        teacher_raw = fields.get("Teacher Name") or fields.get("Teacher", "")
        self.teacher = _text(teacher_raw, "Unknown Teacher")
        self.teachers = _all(teacher_raw)
        self.community = _text(fields.get("School Community", ""), "Unknown Community")
        self.building = self.school
        self.phone = _text(fields.get("School Lead Phone") or fields.get("Teacher Phone") or
                           fields.get("Provider Phone", ""), "")
        self.notes = _text(fields.get("Session Description", ""), "")
        self.grades = _text(fields.get("Grade(s)", ""), "Unknown")
        self.num_students = _int(fields.get("Students", 1), 1)
        self.length = _int(fields.get("Length (Minutes)", 60), 60)
        self.start_time = parse_airtable_datetime(_text(fields.get("Session Start Date/Time"), ""))
        self.zoom_link = _text(fields.get("WebEx/Zoom Link", ""), "")
        self.status = _text(fields.get("Status", "Unknown"), "Unknown")
        self.school_pt = _text(fields.get("School P/T", ""), "")
        self.gn_ticket_id = _text(fields.get("GN Ticket ID", ""), "")
        self.school_lead_text = _text(fields.get("School Lead Text", ""), "")
        self.gn_ticket_requested = fields.get("GN Ticket Requested", False)
        self.teacher_email = _text(fields.get("Teacher Email", ""), "")
        self.teacher_emails = _all(fields.get("Teacher Email", ""))
        # IANA zone name, e.g. 'America/Iqaluit'.
        self.timezone = _text(fields.get("School Timezone", ""), "")
        # Used to decide which of two clashing sessions was booked first.
        self.created_at = record_data.get("createdTime", "")

        # Conflict detection fills these in; see conflict.CONFLICT_DEFAULTS.
        self.is_conflict = False
        self.conflict_details = ""
        self.conflict_type = None

    def __str__(self):
        when = self.start_time.strftime("%Y-%m-%d %H:%M") if self.start_time else "no date"
        return f"Session: {self.title} at {self.school} on {when}"


class AirtableIntegration:
    def __init__(self, api_key, base_id=DEFAULT_BASE_ID, table_name=DEFAULT_TABLE_NAME):
        self.api_key = api_key
        self.base_id = base_id
        self.table_name = table_name
        self.base_url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        # Per client rather than per module: one scan paginates through several
        # requests and reuses the connection, and nothing is shared across threads.
        self.http = _build_http_session()

    def _list(self, filter_formula=None, sort_field=None):
        """Every record matching the formula, following Airtable's pagination."""
        sessions = []
        offset = None
        while True:
            params = {"pageSize": 100}
            if filter_formula:
                params["filterByFormula"] = filter_formula
            if sort_field:
                params["sort[0][field]"] = sort_field
                params["sort[0][direction]"] = "asc"
            if offset:
                params["offset"] = offset

            try:
                response = self.http.get(self.base_url, headers=self.headers, params=params,
                                         timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                body = getattr(getattr(e, "response", None), "text", "")
                logger.error("Airtable list failed: %s %s", e, body[:300])
                raise Exception(f"Failed to fetch sessions from Airtable: {e}")

            data = response.json()
            for record in data.get("records", []):
                try:
                    sessions.append(AirtableSession(record))
                except Exception as e:
                    logger.warning("Could not read session record %s: %s",
                                   record.get("id", "unknown"), e)
            offset = data.get("offset")
            if not offset:
                return sessions

    def get_sessions(self, status_filters=None, user_email=None, window_past_days=14,
                     window_future_days=90):
        """Sessions, optionally narrowed to one school lead's upcoming Nunavut
        bookings that have not been ticketed yet.

        window_past_days is accepted for symmetry with the conflict lookup but does
        not narrow this query: candidates are only ever sessions that have not
        happened yet, so the backward edge is NOW().
        """
        filter_parts = []
        if status_filters:
            filter_parts.append(_any_of("Status", status_filters))

        if user_email:
            filter_parts.append(f"{{School Lead Email}} = {quote(user_email)}")
            filter_parts.append("FIND('NU', {School P/T}) > 0")
            filter_parts.append("NOT(FIND('#gn-submitted', {GN Ticket ID}) > 0)")
            # A session already under way or finished cannot usefully be ticketed,
            # and NOW() rather than TODAY() means one earlier today is excluded too.
            filter_parts.append("IS_AFTER({Session Start Date/Time}, NOW())")
            filter_parts.append(
                f"IS_BEFORE({{Session Start Date/Time}}, {future_cutoff(window_future_days)})"
            )
            filter_parts.append("NOT({GN Ticket Requested} = TRUE())")

        formula = None
        if len(filter_parts) == 1:
            formula = filter_parts[0]
        elif filter_parts:
            formula = f"AND({', '.join(filter_parts)})"

        sessions = self._list(formula, sort_field="Session Start Date/Time")
        logger.info("Found %d upcoming Nunavut session(s)%s not yet processed.",
                    len(sessions), f" for {user_email}" if user_email else "")
        return sessions

    def get_booked_sessions(self, user_email=None, window_past_days=14, window_future_days=90):
        return self.get_sessions(
            status_filters=["Booked"],
            user_email=user_email,
            window_past_days=window_past_days,
            window_future_days=window_future_days,
        )

    def get_all_sessions_for_schools(self, school_names, status_filters=None,
                                     window_past_days=14, window_future_days=90):
        """Every session at these schools inside the window, for conflict checking."""
        if not school_names:
            return []

        filter_parts = [_any_of("School Name Text", school_names)]
        if status_filters:
            filter_parts.append(_any_of("Status", status_filters))
        filter_parts.append(
            f"IS_AFTER({{Session Start Date/Time}}, DATEADD(TODAY(), -{int(window_past_days)}, 'day'))"
        )
        filter_parts.append(
            f"IS_BEFORE({{Session Start Date/Time}}, {future_cutoff(window_future_days)})"
        )

        sessions = self._list(f"AND({', '.join(filter_parts)})")
        logger.info("Found %d existing session(s) across %d school(s) for conflict checking.",
                    len(sessions), len(school_names))
        return sessions

    def get_record(self, session_id):
        """One record's fields, fresh from Airtable."""
        url = f"{self.base_url}/{session_id}"
        try:
            # The whole record, deliberately: Airtable's retrieve-a-record endpoint
            # takes no "fields" parameter — only list-records does — and rejects the
            # request with a 422 if one is sent.
            response = self.http.get(url, headers=self.headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("Error reading %s: %s", session_id, e)
            raise Exception(f"Failed to read session record: {e}")
        return response.json().get("fields", {})

    def update_session_fields(self, session_id, fields):
        """Set fields on a record. Raises if Airtable did not accept the write."""
        url = f"{self.base_url}/{session_id}"
        try:
            response = self.http.patch(url, headers=self.headers,
                                       json={"fields": fields, "typecast": True},
                                       timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error("Error updating Airtable record %s: %s", session_id, e)
            raise Exception(f"Failed to update session field: {e}")

    def update_session_field(self, session_id, field_name, value):
        return self.update_session_fields(session_id, {field_name: value})

    def append_session_note(self, session_id, field_name, note):
        """Add a line to a free-text field without losing what is already there.

        Host notes are written by people — "Joining via zoom in the classroom", who
        is late, which mic the room has — so this appends rather than replacing. A
        note already present is left alone, which makes clicking twice harmless.
        """
        existing = (self.get_record(session_id).get(field_name) or "").strip()
        if note.strip() in existing:
            return existing
        combined = f"{existing}\n{note}".strip() if existing else note
        self.update_session_field(session_id, field_name, combined)
        return combined


def create_airtable_client(api_key):
    """Create an Airtable client with the given API key"""
    return AirtableIntegration(api_key)
