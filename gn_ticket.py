# -*- coding: utf-8 -*-
import time
from datetime import timedelta
import json
import pyotp

import requests
from pytz import timezone
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, ElementNotInteractableException, \
    StaleElementReferenceException, NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.wait import WebDriverWait

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.alert import Alert

# Progress tracking function placeholder
set_progress_func = lambda *args, **kwargs: None


def set_progress(session_id, message, step=None, total_steps=None, status="running"):
    """Placeholder progress reporter."""
    set_progress_func(session_id, message, step, total_steps, status)

def generate_totp_token(secret):
    """Generate TOTP token using user's secret"""
    try:
        totp = pyotp.TOTP(secret)
        token = totp.now()
        return token
    except Exception as e:
        print(f"Error generating TOTP token: {e}")
        raise


def gn_ticket_handler(book_sessions, username, pw, zoom_account, progress_session_id=None, airtable_api_key=None,
                      totp_secret=None, headless_mode=True, chatgpt_api_key=None, allow_manual_site_selection=False):
    """
    Process GN ticket submissions for booked sessions.

    Args:
        book_sessions: List of session objects to process
        username: ServiceNow username (user's email)
        pw: ServiceNow password
        zoom_account: Zoom account email (hardcoded to connectednorth@takingitglobal.org)
        progress_session_id: ID for progress tracking
        airtable_api_key: User's Airtable API key
        totp_secret: User's TOTP secret for 2FA
        headless_mode: Boolean - True for headless, False to show browser
        chatgpt_api_key: OpenAI API key for smart site matching
        allow_manual_site_selection: Boolean - True to allow user to manually select site if automated fails
    """
    # Validate required parameters
    if not airtable_api_key:
        set_progress(progress_session_id, "Missing Airtable API key", status="error")
        raise ValueError("Airtable API key is None")

    total_sessions = len(book_sessions)
    current_session = 0

    browser_mode_msg = "headless mode" if headless_mode else "visible browser mode"
    set_progress(progress_session_id, f"Setting up Chrome browser in {browser_mode_msg}...", 1, 8)

    # Set up Chrome options
    options = webdriver.ChromeOptions()

    if headless_mode:
        # Enable headless mode
        options.add_argument("--headless")

    # Recommended options for better stability (apply regardless of headless mode)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")

    # Optional: Reduce logging noise
    options.add_argument("--log-level=3")
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    options.add_experimental_option('useAutomationExtension', False)

    # Optional: Set user agent to avoid detection
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

    # Start the webdriver
    driver = webdriver.Chrome(options=options)

    if not headless_mode:
        set_progress(progress_session_id, "🖥️  Browser window opened - you can watch the process!", 2, 8)

    set_progress(progress_session_id, "Navigating to ServiceNow login page...", 2, 8)

    try:
        # Log in once for 2FA
        driver.get("https://nunavutprod.service-now.com/login.do")

        set_progress(progress_session_id, "Entering login credentials...", 3, 8)

        username_box = driver.find_element(By.ID, "user_name")
        username_box.send_keys(username)
        pw_box = driver.find_element(By.ID, "user_password")
        pw_box.send_keys(pw)
        login_button = driver.find_element(By.ID, "sysverb_login")
        login_button.click()
        time.sleep(1)

        set_progress(progress_session_id, "Generating 2FA token...", 4, 8)

        # Generate 2FA token using user's TOTP secret
        token = generate_totp_token(totp_secret)
        print(f"Generated 2FA token: {token}")

        set_progress(progress_session_id, "Entering 2FA token...", 5, 8)

        input_box = driver.find_element(By.ID, "txtResponse")
        input_box.send_keys(token)
        time.sleep(0.5)
        input_box.send_keys(Keys.ENTER)

        set_progress(progress_session_id, "Waiting for login to complete...", 6, 8)

        ready = WebDriverWait(driver, 30).until(expected_conditions.url_to_be("https://nunavutprod.service-now.com/sp"))

        set_progress(progress_session_id, "Login successful! Processing sessions...", 7, 8)

        # Track results with more detail
        successful_sessions = []
        failed_sessions = []
        warning_sessions = []

        # Process each session
        for cn_session in book_sessions:
            current_session += 1
            session_progress_msg = f"Processing session {current_session}/{total_sessions}: {cn_session.title}"
            set_progress(progress_session_id, session_progress_msg, 8, 8, "running")

            print("Processing", cn_session.title, "at", cn_session.school)

            try:
                # Verify Zoom meeting exists in Airtable
                set_progress(progress_session_id, f"Checking Zoom meeting for {cn_session.title}...", 8, 8, "running")
                zoom_check_result = check_zoom_meeting(cn_session, airtable_api_key)

                if not zoom_check_result:
                    set_progress(progress_session_id, f"⚠️  No Zoom link found for {cn_session.title}", 8, 8, "running")
                    warning_sessions.append({
                        'title': cn_session.title,
                        'reason': 'No Zoom link found'
                    })
                else:
                    set_progress(progress_session_id, f"✅ Zoom meeting confirmed for {cn_session.title}", 8, 8,
                                 "running")

                # Submit GN ticket
                set_progress(progress_session_id, f"Submitting GN ticket for {cn_session.title}...", 8, 8, "running")
                ticket_result = do_gn_ticket(driver, cn_session, username, pw, progress_session_id, airtable_api_key,
                                             chatgpt_api_key, allow_manual_site_selection, headless_mode)

                # Mark as successfully requested in Airtable
                set_airtable_field(cn_session, "GN Ticket Requested", True, airtable_api_key)

                successful_sessions.append({
                    'title': cn_session.title,
                    'ticket_id': ticket_result.get('ticket_id', 'Unknown')
                })
                set_progress(progress_session_id, f"✅ Completed {cn_session.title}", 8, 8, "running")

            except Exception as e:
                error_msg = f"❌ Error processing {cn_session.title}: {str(e)}"
                failed_sessions.append({
                    'title': cn_session.title,
                    'error': str(e)
                })
                set_progress(progress_session_id, error_msg, 8, 8)
                print(f"Error processing {cn_session.title}: {repr(e)}")

        # Create detailed completion message
        total_processed = len(successful_sessions) + len(failed_sessions) + len(warning_sessions)
        completion_msg = f"Processing complete! {len(successful_sessions)} successful"

        if failed_sessions:
            failed_titles = [s['title'] for s in failed_sessions]
            completion_msg += f", {len(failed_sessions)} failed ({', '.join(failed_titles)})"

        if warning_sessions:
            warning_titles = [s['title'] for s in warning_sessions]
            completion_msg += f", {len(warning_sessions)} with warnings ({', '.join(warning_titles)})"

        completion_msg += f". Total: {total_processed} sessions."

        if not headless_mode:
            completion_msg += " You can close the browser window now."
        set_progress(progress_session_id, completion_msg, 8, 8, "completed")

    except Exception as e:
        error_msg = f"Critical error during processing: {str(e)}"
        set_progress(progress_session_id, error_msg, 8, 8, "error")
        # Return error information
        return {
            'successful_sessions': [],
            'failed_sessions': [{'title': 'Critical Error', 'error': str(e)}],
            'warning_sessions': []
        }
    finally:
        if not headless_mode:
            # Give user time to see the final result before closing
            time.sleep(3)
        driver.quit()

    # Return detailed results
    return {
        'successful_sessions': successful_sessions,
        'failed_sessions': failed_sessions,
        'warning_sessions': warning_sessions
    }


def check_zoom_meeting(the_session, api_key):
    """Check if a Zoom meeting link exists for the session in Airtable"""
    try:
        response = requests.get(f"https://api.airtable.com/v0/appP1kThwW9zVEpHr/Sessions/{the_session.s_id}",
                                headers={"Authorization": "Bearer " + api_key})
        airtable_response = response.json()

        zoom_link = airtable_response.get('fields', {}).get('WebEx/Zoom Link', '')

        if zoom_link and zoom_link.strip() and zoom_link != '':
            print(f"Zoom link found for {the_session.title}: {zoom_link}")
            return True
        else:
            print(f"No Zoom link found for {the_session.title}")
            return False

    except Exception as e:
        print(f"Error checking Zoom link for {the_session.title}: {e}")
        return False


def get_all_dropdown_options_from_html(driver, element_id_to_click, results_css_selector="ul.select2-results"):
    """
    Clicks an element to open a dropdown, extracts all options, then closes the dropdown.
    Returns a list of option texts.
    """
    try:
        set_progress_func(None,
                          f"DEBUG SITE: get_all_dropdown_options_from_html: Starting for '{element_id_to_click}'.",
                          None, None)

        # 1. Find the main container element (e.g., s2id_sp_formfield_select_sites)
        dropdown_container = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, element_id_to_click))
        )

        # 2. Click the container to activate the select2 dropdown
        try:
            dropdown_container.click()
            set_progress_func(None,
                              f"DEBUG SITE: get_all_dropdown_options_from_html: Clicked container '{element_id_to_click}'.",
                              None, None)
        except ElementClickInterceptedException:
            set_progress_func(None,
                              f"DEBUG SITE: get_all_dropdown_options_from_html: ElementClickInterceptedException for '{element_id_to_click}', trying JS click.",
                              None, None, "warning")
            driver.execute_script("arguments[0].click();", dropdown_container)
        except StaleElementReferenceException:
            set_progress_func(None,
                              f"DEBUG SITE: get_all_dropdown_options_from_html: StaleElementReferenceException for '{element_id_to_click}', re-finding and trying JS click.",
                              None, None, "warning")
            dropdown_container = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, element_id_to_click)))
            driver.execute_script("arguments[0].click();", dropdown_container)

        time.sleep(0.5)  # Short wait for UI to react

        # 3. Find the actual visible input field for typing (e.g., id="s2id_autogenXX")
        # This input often has class 'select2-input' and is within the select2-container-active or select2-drop-active
        search_input_field = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, f"#{element_id_to_click} .select2-input, .select2-drop-active .select2-input"))
        )
        set_progress_func(None,
                          f"DEBUG SITE: get_all_dropdown_options_from_html: Found select2 search input field: {search_input_field.get_attribute('id') or search_input_field.tag_name}",
                          None, None)

        # Type a space and backspace to force populate all results if they don't show automatically
        search_input_field.send_keys(" ")
        search_input_field.send_keys(Keys.BACK_SPACE)
        time.sleep(1)  # Give results time to load

        # 4. Wait for the results container to be visible. Use aria-owns for more dynamic id targeting
        results_id = search_input_field.get_attribute("aria-owns")
        results_css_selector_dynamic = f"#{results_id}" if results_id else results_css_selector
        WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, results_css_selector_dynamic))
        )
        set_progress_func(None,
                          f"DEBUG SITE: get_all_dropdown_options_from_html: Dropdown results container '{results_css_selector_dynamic}' is visible.",
                          None, None)
        time.sleep(1)  # Give it a moment to fully render

        # Extract all option texts
        option_elements = driver.find_elements(By.CSS_SELECTOR, f"{results_css_selector_dynamic} .select2-result-label")
        options = [elem.text.strip() for elem in option_elements if
                   elem.text.strip() not in ["No matches found", "Searching...", "Loading...", ""]]

        # Close the dropdown by pressing ESC
        search_input_field.send_keys(Keys.ESCAPE)  # Send ESC to the active input field
        set_progress_func(None, f"DEBUG SITE: get_all_dropdown_options_from_html: Closed dropdown with ESCAPE key.",
                          None, None)
        time.sleep(0.5)  # Short wait after closing

        set_progress_func(None, f"DEBUG SITE: Successfully extracted {len(options)} valid options.", None, None)
        return options
    except Exception as e:
        set_progress_func(None, f"DEBUG SITE: Error in get_all_dropdown_options_from_html: {e}", None, None, "error")
        print(f"Error in get_all_dropdown_options_from_html: {e}")

        # Attempt to close dropdown if it's open to prevent interference
        try:
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
            time.sleep(0.5)
        except:
            pass  # Ignore errors during defensive closing
        return []


def ask_chatgpt_for_best_match(dropdown_options, community_name, school_name, api_key=None):
    """
    Ask ChatGPT to select the best matching site from dropdown options

    Args:
        dropdown_options: List of available site options from dropdown
        community_name: Community name from Airtable
        school_name: School name from Airtable
        api_key: OpenAI API key

    Returns:
        Best matching option or None
    """
    if not dropdown_options or not api_key:
        set_progress_func(None, f"DEBUG SITE: ChatGPT skipped (no options or API key).", None, None)
        return None

    # Prepare the prompt for ChatGPT
    prompt = f"""You are helping to match a school location to the correct ServiceNow site entry. 

School Information:
- Community: {community_name}
- School Name: {school_name}

Available Site Options from ServiceNow dropdown:
{chr(10).join([f"- {option}" for option in dropdown_options])}

Please select the EXACT text of the most appropriate site option from the list above that best matches this school location. Consider:
1. Community name matching
2. School name matching  
3. Geographic proximity
4. Common abbreviations or variations

Respond with ONLY the exact text of your chosen option, nothing else."""

    try:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': 'gpt-3.5-turbo',
            'messages': [
                {'role': 'user', 'content': prompt}
            ],
            'max_tokens': 100,
            'temperature': 0.1
        }

        response = requests.post('https://api.openai.com/v1/chat/completions',
                                 headers=headers, json=data, timeout=30)

        if response.status_code == 200:
            result = response.json()
            suggested_match = result['choices'][0]['message']['content'].strip()

            # Verify the suggestion is actually in our options
            for option in dropdown_options:
                if option.strip().lower() == suggested_match.lower():
                    set_progress_func(None, f"DEBUG SITE: ChatGPT suggested valid option: '{option}'.", None, None)
                    return option

            set_progress_func(None,
                              f"DEBUG SITE: ChatGPT suggested '{suggested_match}' but it's not in available options.",
                              None, None, "warning")
            print(f"ChatGPT suggested '{suggested_match}' but it's not in available options")
            return None
        else:
            set_progress_func(None, f"DEBUG SITE: ChatGPT API error: {response.status_code} - {response.text}", None,
                              None, "error")
            print(f"ChatGPT API error: {response.status_code}")
            return None

    except Exception as e:
        set_progress_func(None, f"DEBUG SITE: Error calling ChatGPT API: {e}", None, None, "error")
        print(f"Error calling ChatGPT API: {e}")
        return None


def get_valid_options(options):
    """Filter out invalid/error message options and return only valid ones (redundant, but keeping for safety)"""
    if not options:
        return []

    valid_options = []
    for option_str in options:  # Renamed 'option' to 'option_str' to avoid confusion with `option` as a `Select` element
        option_lower = option_str.lower().strip()
        if option_lower not in ["no matches found", "searching...", "loading...", "", "no results"]:
            valid_options.append(option_str)

    return valid_options


def try_dropdown_selection(driver, element_id, text, wait_time):
    """Select an option from a ServiceNow select2 dropdown.

    This implementation falls back to the simpler approach used in the
    original version of the script which proved to be more reliable.  The
    element is brought into view, clicked, and then the active element is used
    to type the desired text and confirm with ENTER.

    Args:
        driver: Active Selenium WebDriver instance.
        element_id: ID attribute of the select2 container to interact with.
        text: Text to search for within the dropdown.
        wait_time: Seconds to wait between interactions.
    """
    try:
        dropdown_container = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, element_id))
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
            dropdown_container,
        )
        time.sleep(wait_time)

        try:
            dropdown_container.click()
        except (ElementClickInterceptedException, StaleElementReferenceException):
            driver.execute_script("arguments[0].click();", dropdown_container)
        time.sleep(wait_time)

        active_element = driver.switch_to.active_element
        active_element.send_keys(text)
        time.sleep(wait_time * 2)
        active_element.send_keys(Keys.ENTER)
        time.sleep(wait_time)
        return True
    except Exception as e:
        set_progress_func(
            None,
            f"DEBUG SITE: Dropdown selection failed for '{text}' in {element_id}: {e}",
            None,
            None,
            "error",
        )
        print(f"Dropdown selection failed for '{text}': {e}")
        return False
    finally:
        # Ensure dropdown is closed before moving on
        try:
            driver.switch_to.active_element.send_keys(Keys.ESCAPE)
        except Exception:
            pass


def get_first_word(text):
    """Extract the first word from a text string for better ServiceNow matching"""
    if not text:
        return ""

    # Split by spaces and take the first word
    words = text.strip().split()
    if words:
        first_word = words[0]
        # Remove common punctuation that might interfere
        first_word = first_word.rstrip('.,;:!?-')
        return first_word

    return text


def get_first_two_words(text):
    """Extract the first two words from a text string."""
    if not text:
        return ""
    words = text.strip().split()
    if len(words) >= 2:
        return " ".join(words[:2]).rstrip('.,;:!?-')
    elif words:
        return words[0].rstrip('.,;:!?-')
    return ""


def get_site_name(community, building):
    """Generate site name using full community name and first word of building for better ServiceNow matching"""
    # Use full community name (it might be multiple words)
    building_first = get_first_word(building)
    return f"{community} {building_first}".strip()


def smart_site_selection(driver, cn_session, wait_time=1.5, progress_session_id=None, chatgpt_api_key=None,
                         allow_manual_selection=False, headless_mode=True):
    """
    Intelligently select the site field by first collecting all options, then matching.
    Includes manual intervention fallback.
    """
    element_id = "s2id_sp_formfield_select_sites"  # The ID of the outer container/clickable element

    set_progress_func(progress_session_id,
                      f"DEBUG SITE: Starting smart site selection for '{cn_session.school}' in '{cn_session.community}'",
                      None, None)

    # Step 1: Get all available options from the dropdown
    all_site_options = get_all_dropdown_options_from_html(driver, element_id)

    if not all_site_options:
        set_progress_func(progress_session_id,
                          "DEBUG SITE: No site options could be retrieved. Cannot proceed with any automated or manual selection.",
                          None, None, "error")
        return False

    # Step 2: Prepare candidate search terms for internal matching
    candidates = []

    # Special cases (exact matches)
    if "Ataguttaaluk" in cn_session.school:
        candidates.append("Igloolik")
    if "Kugluktuk" in cn_session.school:
        candidates.append("Kugluktuk")

    # Generate combinations from Airtable data
    community = cn_session.community.strip()
    school = cn_session.school.strip()
    building = cn_session.building.strip()

    building_first_word = get_first_word(building)
    building_first_two_words = get_first_two_words(building)  # Can be same as first word if only one word
    school_first_word = get_first_word(school)
    school_first_two_words = get_first_two_words(school)  # Can be same as first word if only one word

    # Prioritized candidates (ordered from most specific to less specific combinations)
    candidates.append(f"{community} {building_first_word}".strip())
    if building_first_two_words and building_first_two_words != building_first_word:
        candidates.append(f"{community} {building_first_two_words}".strip())
    candidates.append(f"{community} {school_first_word}".strip())  # Community + School first word
    if school_first_two_words and school_first_two_words != school_first_word:
        candidates.append(f"{community} {school_first_two_words}".strip())

    # Secondary candidates
    candidates.append(building_first_word)
    if building_first_two_words and building_first_two_words != building_first_word:
        candidates.append(building_first_two_words)
    candidates.append(school_first_word)
    if school_first_two_words and school_first_two_words != school_first_word:
        candidates.append(school_first_two_words)

    candidates.append(community)

    # Remove duplicates and empty strings, maintain order as much as possible
    unique_candidates = []
    [unique_candidates.append(x) for x in candidates if x and x not in unique_candidates]

    set_progress_func(progress_session_id, f"DEBUG SITE: Internal Candidates (ordered): {unique_candidates}", None,
                      None)

    # Step 3: Iterate through candidates and find an exact match in all_site_options (code-driven)
    found_exact_match = None
    for candidate in unique_candidates:
        if not candidate: continue  # Skip empty candidates
        for option in all_site_options:
            if option.strip().lower() == candidate.strip().lower():
                found_exact_match = option
                break
        if found_exact_match:
            break

    if found_exact_match:
        set_progress_func(progress_session_id,
                          f"DEBUG SITE: Found exact match '{found_exact_match}'. Attempting selection...", None, None)
        if try_dropdown_selection(driver, element_id, found_exact_match, wait_time):
            set_progress_func(progress_session_id, f"DEBUG SITE: Successfully selected '{found_exact_match}'.", None,
                              None)
            return True
        else:
            set_progress_func(progress_session_id,
                              f"DEBUG SITE: Failed to select exact match '{found_exact_match}'. Clearing field and proceeding to next attempt.",
                              None, None, "warning")
            # If selection failed, reset the input field before next attempt
            try_dropdown_selection(driver, element_id, "", 0.1)  # Clear the field
            time.sleep(0.5)
    else:
        set_progress_func(progress_session_id,
                          "DEBUG SITE: No exact match found in internal candidate list. Proceeding to ChatGPT.", None,
                          None)

    # Step 4: ChatGPT-Assisted Matching (Filtered by Community)
    # Filter options to only those containing the community name for more relevant ChatGPT suggestions
    community_lower = cn_session.community.strip().lower()
    community_filtered_options = [
        option for option in all_site_options
        if community_lower in option.strip().lower()
    ]

    if not community_filtered_options:
        set_progress_func(progress_session_id,
                          f"DEBUG SITE: No site options found containing community '{cn_session.community}'. Skipping ChatGPT for filtered list.",
                          None, None, "warning")
    else:
        set_progress_func(progress_session_id,
                          f"DEBUG SITE: Found {len(community_filtered_options)} options containing '{cn_session.community}'. Consulting ChatGPT.",
                          None, None)

    # Step 4: ChatGPT fallback if no exact match found or selection failed (only if options were retrieved)
    # Use community_filtered_options for ChatGPT if available, otherwise use all_site_options
    # Only proceed if chatgpt_api_key is available
    if chatgpt_api_key:
        set_progress_func(progress_session_id, "DEBUG SITE: Calling ChatGPT for best match...", None, None)
        target_options_for_chatgpt = community_filtered_options if community_filtered_options else all_site_options
        best_match = ask_chatgpt_for_best_match(target_options_for_chatgpt, cn_session.community, cn_session.school,
                                                chatgpt_api_key)

        if best_match:
            set_progress_func(progress_session_id,
                              f"DEBUG SITE: ChatGPT suggested '{best_match}'. Attempting selection...", None, None)
            if try_dropdown_selection(driver, element_id, best_match, wait_time):
                set_progress_func(progress_session_id,
                                  f"DEBUG SITE: Successfully selected ChatGPT's suggestion: '{best_match}'.", None,
                                  None)
                return True
            else:
                set_progress_func(progress_session_id,
                                  f"DEBUG SITE: Failed to select ChatGPT's suggestion: '{best_match}'. Clearing field and proceeding.",
                                  None, None, "warning")
                # If selection failed, reset the input field before next attempt
                try_dropdown_selection(driver, element_id, "", 0.1)  # Clear the field
                time.sleep(0.5)
        else:
            set_progress_func(progress_session_id,
                              "DEBUG SITE: ChatGPT couldn't determine a best match or suggested an invalid option.",
                              None, None, "warning")
    elif not chatgpt_api_key:  # Log if API key is missing
        set_progress_func(progress_session_id, "DEBUG SITE: ChatGPT API key not configured. Skipping ChatGPT.", None,
                          None, "warning")

    # Step 5: Manual Intervention
    if allow_manual_selection and not headless_mode:  # Only prompt for manual if enabled and browser is visible
        set_progress_func(progress_session_id, "AUTOMATIC SITE SELECTION FAILED. Manual intervention required.", None,
                          None, "warning")
        print("\n" + "=" * 80)
        print("  AUTOMATIC SITE SELECTION FAILED for:")
        print(f"  Session: {cn_session.title}")
        print(f"  School: {cn_session.school}")
        print(f"  Community: {cn_session.community}")
        print(f"  (Current URL: {driver.current_url})")  # Add current URL for debugging
        print("\n  PLEASE MANUALLY SELECT THE CORRECT SITE IN THE BROWSER WINDOW.")
        print("  Then, type the EXACT text of your selection below and press Enter.")
        print("  (Type 'skip' or 's' to skip this session)")
        print("=" * 80 + "\n")

        driver.maximize_window()  # Ensure browser is visible for interaction
        manual_site_input = input("Enter site name or 'skip': ").strip()

        if manual_site_input.lower() in ['skip', 's']:
            set_progress_func(progress_session_id, "User chose to skip manual site selection for this session.", None,
                              None, "error")
            return False
        elif try_dropdown_selection(driver, element_id, manual_site_input, wait_time):
            set_progress_func(progress_session_id, f"User manually selected '{manual_site_input}'.", None, None,
                              "completed")
            return True
        else:
            set_progress_func(progress_session_id,
                              f"DEBUG SITE: Manual selection of '{manual_site_input}' failed. Proceeding to final fallback.",
                              None, None, "error")
            print(f"Manual selection of '{manual_site_input}' failed. Please check the exact spelling.")

    elif allow_manual_selection and headless_mode:  # If manual is enabled but headless, it's impossible
        set_progress_func(progress_session_id,
                          "DEBUG SITE: Manual site selection requested, but browser is in headless mode. Skipping manual intervention.",
                          None, None, "warning")

    # If we reach here, all automated and manual methods have failed
    set_progress_func(progress_session_id, "DEBUG SITE: All site selection methods failed. Could not select a site.",
                      None, None, "error")
    return False


def get_zoom_digits(cn_session, api_key):
    """Extract Zoom meeting ID from the Zoom link in Airtable"""
    try:
        response = requests.get(f"https://api.airtable.com/v0/appP1kThwW9zVEpHr/Sessions/{cn_session.s_id}",
                                headers={"Authorization": "Bearer " + api_key})
        airtable_response = response.json()

        zoom_link = str(airtable_response['fields']['WebEx/Zoom Link'])
        # Extract meeting ID from the Zoom URL - typically the last 11 digits
        zoom_digits = zoom_link[-11:]
        print(f"Extracted Zoom digits: {zoom_digits}")
        return zoom_digits
    except Exception as e:
        print(f"Error extracting Zoom digits: {e}")
        return None


def update_sip_url(item, url, api_key):
    """Update SIP URL in Airtable"""
    sip_data = {'fields': {'Bridge Address / SIP URI': url, 'Send Meeting Invite to:': "All"},
                "typecast": True}
    json_data = json.dumps(sip_data)
    response = requests.patch(f"https://api.airtable.com/v0/appP1kThwW9zVEpHr/Sessions/{item.s_id}",
                              headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                              data=json_data)


def set_airtable_field(item, field, content, api_key):
    """Update a field in Airtable"""
    the_data = {'fields': {field: content}, "typecast": True}
    json_data = json.dumps(the_data)
    response = requests.patch(f"https://api.airtable.com/v0/appP1kThwW9zVEpHr/Sessions/{item.s_id}",
                              headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                              data=json_data)


def do_gn_ticket(driver, cn_session, username, pw, progress_session_id=None, api_key=None, chatgpt_api_key=None,
                 allow_manual_site_selection=False, headless_mode=True):
    wait_time = 1.5

    set_progress(progress_session_id, f"Loading GN ticket form for {cn_session.title}...", None, None)

    try:
        driver.get("https://nunavutprod.service-now.com/sp/?id=sc_cat_item&sys_id=35083704dbe305908e611bad139619a5")
        time.sleep(wait_time * 2)
    except Exception as e:
        alert = Alert(driver)
        if alert:
            alert.accept()

    gn_form = driver.find_element(By.TAG_NAME, "body")

    set_progress(progress_session_id, f"Filling form fields for {cn_session.title}...", None, None)

    # Clear any leftover global active element state before starting form fill
    driver.switch_to.active_element.send_keys(Keys.ESCAPE)
    time.sleep(0.5)

    # Select your Department
    set_progress(progress_session_id, f"Setting department...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_select_your_department",
                                  "Connected North", wait_time):
        raise Exception("Failed to set department")

    # Department User
    set_progress(progress_session_id, f"Setting department user...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_department_user",
                                  "Education", wait_time):
        raise Exception("Failed to set department user")

    # Community
    set_progress(progress_session_id, f"Setting community: {cn_session.community}...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_community_video",
                                  cn_session.community, wait_time):
        raise Exception("Failed to set community")

    # Office Phone
    set_progress(progress_session_id, f"Setting phone number...", None, None)
    office_phone = driver.find_element(By.ID, "sp_formfield_office_phone")
    driver.execute_script("arguments[0].scrollIntoView(true);", office_phone)  # Scroll to element before interacting
    office_phone.clear()
    office_phone.send_keys(cn_session.phone)
    time.sleep(wait_time / 2)  # Shorter wait after simple text input

    # Building - Use only first word for better matching
    set_progress(progress_session_id, f"Setting building: {cn_session.building}...", None, None)
    building_search = get_first_word(cn_session.building)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_building_user",
                                  building_search, wait_time):
        raise Exception("Failed to set building")

    # Client Name
    set_progress(progress_session_id, f"Setting client name...", None, None)
    client_name = driver.find_element(By.ID, "sp_formfield_client_name")
    driver.execute_script("arguments[0].scrollIntoView(true);", client_name)  # Scroll to element before interacting
    client_name.clear()
    client_name.send_keys(cn_session.teacher + " at " + cn_session.school)

    # Session topic
    set_progress(progress_session_id, f"Setting session topic...", None, None)
    session_topic = driver.find_element(By.ID, "sp_formfield_session_topic_or_description")
    driver.execute_script("arguments[0].scrollIntoView(true);", session_topic)
    session_topic.clear()
    session_topic.send_keys(cn_session.title)

    # Screen layout
    set_progress(progress_session_id, f"Setting screen layout...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_screen_layout",
                                  "Full", wait_time):
        raise Exception("Failed to set screen layout")

    # Session date YYYY-MM-DD
    set_progress(progress_session_id, f"Setting session date and time...", None, None)
    session_date = driver.find_element(By.ID, "sp_formfield_session_date")
    driver.execute_script("arguments[0].scrollIntoView(true);", session_date)
    session_date.click()
    element = driver.switch_to.active_element

    formatted_date = cn_session.start_time.strftime("%Y-%m-%d")
    element.send_keys(formatted_date)
    time.sleep(wait_time)
    element.send_keys(Keys.ENTER)

    # Timezone setup and formatting
    EST = timezone('US/Eastern')
    start_time_EST = cn_session.start_time.astimezone(EST) - timedelta(minutes=10)
    end_time_EST = start_time_EST + timedelta(minutes=(cn_session.length + 10))

    # Session start time HH:MM AM
    gn_form.send_keys(Keys.TAB)
    gn_form.send_keys(Keys.TAB)

    formatted_time = start_time_EST.strftime("%-I:%M %p")
    element = driver.switch_to.active_element
    element.send_keys(formatted_time)

    # Session end time HH:MM AM
    gn_form.send_keys(Keys.TAB)

    formatted_time = end_time_EST.strftime("%-I:%M %p")
    element = driver.switch_to.active_element
    element.send_keys(formatted_time)
    time.sleep(wait_time)

    # Time zone (always set to Eastern)
    set_progress(progress_session_id, f"Setting timezone...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_time_zone",
                                  "Eastern", wait_time):
        raise Exception("Failed to set timezone")

    # Site - Use smart selection with ChatGPT fallback, and potentially manual intervention
    set_progress(progress_session_id,
                 f"Setting site information with smart matching (and manual fallback if enabled)...", None, None)
    success = smart_site_selection(driver, cn_session, wait_time, progress_session_id, chatgpt_api_key,
                                   allow_manual_site_selection, headless_mode)
    if not success:
        raise Exception(
            f"Failed to set site for {cn_session.title} - all selection methods failed (including manual if enabled).")

    # Connection Details
    set_progress(progress_session_id, f"Setting connection details...", None, None)
    conn_details = driver.find_element(By.ID, "sp_formfield_connection_details")
    driver.execute_script("arguments[0].scrollIntoView(true);", conn_details)  # Scroll to element before interacting
    conn_details.click()
    element = driver.switch_to.active_element
    element.clear()

    try:
        zoom_digits = get_zoom_digits(cn_session, api_key)
        if zoom_digits:
            element.send_keys(zoom_digits + "@zoomcrc.com")
            set_progress(progress_session_id, f"✅ Added Zoom connection details for {cn_session.title}", None, None)
        else:
            element.send_keys("No Zoom meeting found - please add manually")
            set_progress(progress_session_id,
                         f"⚠️ No Zoom meeting found for {cn_session.title} - manual input required", None, None)
    except Exception as e:
        element.send_keys("Error retrieving Zoom details - please add manually")
        set_progress(progress_session_id, f"⚠️ Error retrieving Zoom details for {cn_session.title}: {str(e)}", None,
                     None)

    time.sleep(wait_time)
    element.send_keys(Keys.ENTER)
    time.sleep(wait_time)

    # Submit
    set_progress(progress_session_id, f"Submitting ticket for {cn_session.title}...", None, None)
    submit_btn = driver.find_element(By.ID, "submit-btn")
    driver.execute_script("arguments[0].scrollIntoView(true);", submit_btn)  # Scroll to element before interacting
    submit_btn.click()
    time.sleep(wait_time * 3)

    WebDriverWait(driver, 30).until(expected_conditions.url_contains("&table=sc_request"))

    set_progress(progress_session_id, f"✅ Ticket submitted successfully for {cn_session.title}", None, None)
    set_airtable_field(cn_session, "GN Ticket Requested", True, api_key)  # Always mark as requested if we reach here

    ticket_id = ""  # Initialize ticket_id here to ensure it's always defined
    try:
        req_number_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.text-left.inline.ng-scope"))
        )
        ticket_id = req_number_element.text
        print("req number:", ticket_id)
        # Ensure we don't overwrite existing notes, append ticket ID
        current_notes = cn_session.notes if cn_session.notes else ""
        set_airtable_field(cn_session, "GN Ticket ID", f"{current_notes} #gn-submitted {ticket_id}".strip(), api_key)
        set_progress(progress_session_id, f"✅ Ticket {ticket_id} created for {cn_session.title}", None, None)
    except Exception as e:
        print(f"Could not retrieve ticket ID: {e}")
        ticket_id = "Unknown"

    return {"status": "success", "ticket_id": ticket_id}


# Function to be called by main.py to set the progress function
def set_progress_callback(progress_func):
    global set_progress_func
    set_progress_func = progress_func