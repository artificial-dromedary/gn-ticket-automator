import requests
import json
from datetime import datetime
from dateutil import parser
import pytz
import logging

logger = logging.getLogger(__name__)


class AirtableSession:
    """Represents a session from Airtable"""

    def __init__(self, record_data):
        self.s_id = record_data['id']
        fields = record_data.get('fields', {})

        # Helper function to extract string from array or return as-is
        def extract_text(field_value, default=''):
            if isinstance(field_value, list):
                return field_value[0] if field_value else default
            return field_value or default

        # DEBUG: Check all title-related fields
        title_fields = ['Session Title', 'Session Title Text', 'Session Title Raw',
                        'Subject/Curriculum', 'Primary Subject Text',
                        'Provider Session Name', 'Session Description']

        logger.debug("DEBUG TITLE FIELDS for %s:", self.s_id)
        for field_name in title_fields:
            value = fields.get(field_name, 'NOT FOUND')
            logger.debug("  %s: %s", field_name, value)
        logger.debug("---")

        # Session title - try multiple title field options from your field list
        title_raw = (fields.get('Session Title Text') or
                     fields.get('Session Title Raw') or
                     fields.get('Subject/Curriculum') or
                     fields.get('Primary Subject Text') or
                     fields.get('Session Title') or
                     fields.get('Provider Session Name') or '')
        self.title = extract_text(title_raw, f'Session {self.s_id}')

        # If title is still a record ID, try to get a meaningful title from other fields
        if not self.title or self.title.startswith('rec') or self.title == f'Session {self.s_id}':
            # Try session description as last resort
            desc = extract_text(fields.get('Session Description', ''), '')
            if desc and not desc.startswith('rec'):
                self.title = desc[:50] + "..." if len(desc) > 50 else desc
            else:
                self.title = f"Session {self.s_id}"

        # School - use text field instead of linked record
        school_raw = fields.get('School Name Text', '')
        self.school = extract_text(school_raw, 'Unknown School')

        # Teacher - try both Teacher Name and Teacher
        teacher_raw = fields.get('Teacher Name') or fields.get('Teacher', '')
        self.teacher = extract_text(teacher_raw, 'Unknown Teacher')

        # Community
        community_raw = fields.get('School Community', '')
        self.community = extract_text(community_raw, 'Unknown Community')

        # Building - use school name as fallback
        self.building = self.school

        # Phone numbers
        phone_raw = (fields.get('School Lead Phone') or
                     fields.get('Teacher Phone') or
                     fields.get('Provider Phone', ''))
        self.phone = extract_text(phone_raw, '')

        # Notes/Description
        notes_raw = fields.get('Session Description', '')
        self.notes = extract_text(notes_raw, '')

        # Student info
        grades_raw = fields.get('Grade(s)', '')
        self.grades = extract_text(grades_raw, 'Unknown')

        # Students field
        students_raw = fields.get('Students', 1)
        students_clean = extract_text(students_raw, 1)
        try:
            self.num_students = int(students_clean) if students_clean else 1
        except (ValueError, TypeError):
            self.num_students = 1

        # Session timing
        length_raw = fields.get('Length (Minutes)', 60)
        length_clean = extract_text(length_raw, 60)
        try:
            self.length = int(length_clean) if length_clean else 60
        except (ValueError, TypeError):
            self.length = 60

        # Parse start time
        start_time_raw = fields.get('Session Start Date/Time')
        start_time_str = extract_text(start_time_raw, '')
        if start_time_str:
            try:
                self.start_time = parser.parse(start_time_str)
                # Ensure timezone aware
                if self.start_time.tzinfo is None:
                    self.start_time = pytz.UTC.localize(self.start_time)
            except Exception as e:
                logger.warning("Error parsing date '%s': %s", start_time_str, e)
                self.start_time = datetime.now(pytz.UTC)
        else:
            self.start_time = datetime.now(pytz.UTC)

        # Zoom info
        zoom_raw = fields.get('WebEx/Zoom Link', '')
        self.zoom_link = extract_text(zoom_raw, '')

        # Status
        status_raw = fields.get('Status', 'Unknown')
        self.status = extract_text(status_raw, 'Unknown')

        # School P/T and GN Ticket ID for filtering/debugging
        self.school_pt = extract_text(fields.get('School P/T', ''), '')
        self.gn_ticket_id = extract_text(fields.get('GN Ticket ID', ''), '')
        self.school_lead_text = extract_text(fields.get('School Lead Text', ''), '')
        self.gn_ticket_requested = fields.get('GN Ticket Requested', False)

        # Conflict detection fields
        self.is_conflict = False
        self.conflict_details = ""
        self.conflict_type = None

        # Debug output
        logger.debug("Session: %s | School: %s | Teacher: %s", self.title, self.school, self.teacher)
        logger.debug("   School Lead: %s | P/T: %s", self.school_lead_text, self.school_pt)
        logger.debug("   GN Ticket ID: %s | GN Requested: %s", self.gn_ticket_id, self.gn_ticket_requested)

    def __str__(self):
        """String representation for debugging"""
        return f"Session: {self.title} at {self.school} on {self.start_time.strftime('%Y-%m-%d %H:%M')}"

class AirtableIntegration:
    def __init__(self, api_key, base_id="appP1kThwW9zVEpHr", table_name="Sessions"):
        self.api_key = api_key
        self.base_id = base_id
        self.table_name = table_name
        self.base_url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def get_sessions(self, status_filters=None, user_email=None, window_past_days=14, window_future_days=90):
        """
        Get sessions from Airtable with optional filtering

        Args:
            status_filters: List of status values to filter by (e.g., ["Booked", "Confirmed for Booking"])
            user_email: Filter by user email (for School Lead Email field)

        Returns:
            List of AirtableSession objects
        """
        sessions = []
        offset = None

        while True:
            # Build filter formula
            filter_parts = []

            if status_filters:
                # FIXED: Proper single quotes in filter formula
                status_conditions = [f"{{Status}} = '{status}'" for status in status_filters]
                if len(status_conditions) == 1:
                    filter_parts.append(status_conditions[0])
                else:
                    filter_parts.append(f"OR({', '.join(status_conditions)})")

            if user_email:
                # Use School Lead Email for matching instead of name
                filter_parts.append(f"{{School Lead Email}} = '{user_email}'")

                # ADDED: Filter for Nunavut sessions only
                filter_parts.append("FIND('NU', {School P/T}) > 0")

                # ADDED: Exclude already processed sessions
                filter_parts.append("NOT(FIND('#gn-submitted', {GN Ticket ID}) > 0)")

                # ADDED: Only include sessions within the window
                filter_parts.append(
                    f"IS_AFTER({{Session Start Date/Time}}, DATEADD(TODAY(), -{int(window_past_days)}, 'day'))"
                )
                filter_parts.append(
                    f"IS_BEFORE({{Session Start Date/Time}}, DATEADD(TODAY(), {int(window_future_days)}, 'day'))"
                )

                # ADDED: Exclude sessions where GN Ticket has already been requested
                filter_parts.append("NOT({GN Ticket Requested} = TRUE())")

            # Combine filters
            if filter_parts:
                if len(filter_parts) == 1:
                    filter_formula = filter_parts[0]
                else:
                    filter_formula = f"AND({', '.join(filter_parts)})"
            else:
                filter_formula = None

            # Build request parameters
            params = {
                'pageSize': 100,
                # FIXED: Correct field name for sorting
                'sort[0][field]': 'Session Start Date/Time',  # Was: 'Session Date & Time'
                'sort[0][direction]': 'asc'
            }

            # Add filter if we have one
            if filter_formula:
                params['filterByFormula'] = filter_formula

            if offset:
                params['offset'] = offset

            try:
                response = requests.get(self.base_url, headers=self.headers, params=params)
                response.raise_for_status()

                data = response.json()
                # Debug: Print field names from first record (only for first page)
                if not offset and data.get('records') and len(data['records']) > 0:
                    first_record = data['records'][0]
                    status_value = first_record.get('fields', {}).get('Status', 'NOT FOUND')
                    school_pt = first_record.get('fields', {}).get('School P/T', 'NOT FOUND')
                    gn_ticket = first_record.get('fields', {}).get('GN Ticket ID', 'NOT FOUND')
                    school_lead_email = first_record.get('fields', {}).get('School Lead Email', 'NOT FOUND')
                    gn_ticket_requested = first_record.get('fields', {}).get('GN Ticket Requested', 'NOT FOUND')
                    print(f"📋 Status field value: {status_value}")
                    print(f"📋 School P/T field value: {school_pt}")
                    print(f"📋 GN Ticket ID field value: {gn_ticket}")
                    print(f"📋 School Lead Email field value: {school_lead_email}")
                    print(f"📋 GN Ticket Requested field value: {gn_ticket_requested}")

                # Convert records to session objects
                for record in data.get('records', []):
                    try:
                        session = AirtableSession(record)
                        sessions.append(session)
                    except Exception as e:
                        print(f"Error parsing session record {record.get('id', 'unknown')}: {e}")
                        continue

                # Check for more pages
                offset = data.get('offset')
                if not offset:
                    break

            except requests.exceptions.RequestException as e:
                print(f"Error fetching Airtable data: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Response content: {e.response.text}")
                raise Exception(f"Failed to fetch sessions from Airtable: {e}")

        user_filter_msg = f" for user '{user_email}'" if user_email else ""
        print(f"✅ Found {len(sessions)} Nunavut sessions{user_filter_msg} that haven't been processed yet")
        return sessions

    def get_booked_sessions(self, user_email=None, window_past_days=14, window_future_days=90):
        """Get sessions with 'Booked' status"""
        # FIXED: Use the correct status value
        return self.get_sessions(
            status_filters=["Booked"],
            user_email=user_email,
            window_past_days=window_past_days,
            window_future_days=window_future_days,
        )

    def get_all_sessions_for_schools(self, school_names, status_filters=None):
        """
        Get all sessions from Airtable for a specific list of schools and statuses.

        Args:
            school_names: List of school names to filter by.
            status_filters: List of status values to filter by (e.g., ["Booked"])

        Returns:
            List of AirtableSession objects
        """
        if not school_names:
            return []

        sessions = []
        offset = None

        while True:
            # Build filter formula
            filter_parts = []

            # School filter
            school_conditions = [f"{{School Name Text}} = '{name}'" for name in school_names]
            if len(school_conditions) == 1:
                filter_parts.append(school_conditions[0])
            else:
                filter_parts.append(f"OR({', '.join(school_conditions)})")

            # Status filter
            if status_filters:
                status_conditions = [f"{{Status}} = '{status}'" for status in status_filters]
                if len(status_conditions) == 1:
                    filter_parts.append(status_conditions[0])
                else:
                    filter_parts.append(f"OR({', '.join(status_conditions)})")

            # Combine filters
            filter_formula = f"AND({', '.join(filter_parts)})"

            params = {'pageSize': 100, 'filterByFormula': filter_formula}
            if offset:
                params['offset'] = offset

            try:
                response = requests.get(self.base_url, headers=self.headers, params=params)
                response.raise_for_status()
                data = response.json()
                for record in data.get('records', []):
                    try:
                        session = AirtableSession(record)
                        sessions.append(session)
                    except Exception as e:
                        print(f"Error parsing session record {record.get('id', 'unknown')}: {e}")
                        continue
                offset = data.get('offset')
                if not offset:
                    break
            except requests.exceptions.RequestException as e:
                print(f"Error fetching Airtable school session data: {e}")
                raise Exception(f"Failed to fetch sessions from Airtable: {e}")

        print(f"✅ Found {len(sessions)} existing sessions for conflict checking across {len(school_names)} schools.")
        return sessions

    def update_session_field(self, session_id, field_name, value):
        """Update a specific field in a session record"""
        url = f"{self.base_url}/{session_id}"
        data = {
            "fields": {
                field_name: value
            },
            "typecast": True
        }

        try:
            response = requests.patch(url, headers=self.headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error updating Airtable field: {e}")
            raise Exception(f"Failed to update session field: {e}")

    def test_connection(self):
        """Test the Airtable connection and permissions"""
        try:
            # Try to fetch just one record to test access
            params = {'pageSize': 1}
            response = requests.get(self.base_url, headers=self.headers, params=params)
            response.raise_for_status()

            data = response.json()
            record_count = len(data.get('records', []))

            return {
                'success': True,
                'message': f'Successfully connected to Airtable. Found {record_count} sample record(s).',
                'base_id': self.base_id,
                'table_name': self.table_name
            }

        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if response.status_code == 401:
                error_msg = "Invalid API key or insufficient permissions"
            elif response.status_code == 404:
                error_msg = "Base or table not found. Check your configuration."

            return {
                'success': False,
                'message': f'Failed to connect to Airtable: {error_msg}',
                'status_code': getattr(e.response, 'status_code', None)
            }

def create_airtable_client(api_key):
    """Create an Airtable client with the given API key"""
    return AirtableIntegration(api_key)
