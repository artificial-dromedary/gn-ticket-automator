# -*- coding: utf-8 -*-
import concurrent.futures
import os
import time
from datetime import datetime, timedelta
import json
import pyotp
import difflib
import re
import logging

import requests
from pytz import timezone
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
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

from site_list import AVAILABLE_SITES

# ---------------------------------------------------------------------------
# Site cache — stores the live dropdown options scraped from ServiceNow
# ---------------------------------------------------------------------------
_SITE_CACHE_PATH = os.path.join(os.path.expanduser("~"), "GN_Ticket_Automator", "site_cache.json")
_SITE_CACHE_MAX_AGE_DAYS = 30


def load_site_cache():
    """Return (sites_list, is_stale).  Falls back to AVAILABLE_SITES if missing/expired."""
    try:
        if os.path.exists(_SITE_CACHE_PATH):
            with open(_SITE_CACHE_PATH, "r") as f:
                data = json.load(f)
            fetched = datetime.fromisoformat(data["fetched_at"])
            age_days = (datetime.now() - fetched).days
            if age_days < _SITE_CACHE_MAX_AGE_DAYS and data.get("sites"):
                return data["sites"], False   # fresh
    except Exception as e:
        print(f"[site_cache] Could not read cache: {e}")
    return list(AVAILABLE_SITES), True   # stale / missing


def save_site_cache(sites):
    """Persist scraped sites list to disk."""
    try:
        os.makedirs(os.path.dirname(_SITE_CACHE_PATH), exist_ok=True)
        with open(_SITE_CACHE_PATH, "w") as f:
            json.dump({"fetched_at": datetime.now().isoformat(), "sites": sites}, f, indent=2)
        print(f"[site_cache] Saved {len(sites)} sites.")
    except Exception as e:
        print(f"[site_cache] Could not write cache: {e}")


# ---------------------------------------------------------------------------
# Progress tracking function placeholder
# ---------------------------------------------------------------------------
set_progress_func = lambda *args, **kwargs: None


def set_progress(session_id, message, step=None, total_steps=None, status="running", session_ref=None):
    """Placeholder progress reporter."""
    set_progress_func(session_id, message, step, total_steps, status, session_ref=session_ref)

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
                      totp_secret=None, headless_mode=True, chatgpt_api_key=None, allow_manual_site_selection=False,
                      buffer_before=10, buffer_after=10):
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
        buffer_before: Minutes to start session early
        buffer_after: Minutes to extend session after
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

    # In a container the browser and driver are installed at fixed paths; locally these
    # are unset and Selenium Manager resolves them itself.
    chrome_binary = os.getenv("CHROME_BINARY")
    if chrome_binary:
        options.binary_location = chrome_binary

    chromedriver_path = os.getenv("CHROMEDRIVER")
    service = ChromeService(executable_path=chromedriver_path) if chromedriver_path else None

    # Start the webdriver
    try:
        driver = webdriver.Chrome(options=options, service=service) if service \
            else webdriver.Chrome(options=options)
    except Exception as e:
        logging.error("Failed to start Chrome webdriver", exc_info=True)
        set_progress(progress_session_id, f"❌ Failed to start Chrome browser: {e}", 1, 8, "error")
        raise

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
        driver.execute_script("arguments[0].click();", login_button)

        set_progress(progress_session_id, "Waiting for 2FA page...", 4, 8)

        # ServiceNow shows the 2FA form on the same login.do page.
        # Wait up to 20s for txtResponse to appear (main frame, then iframes).
        input_box = None
        try:
            input_box = WebDriverWait(driver, 20).until(
                expected_conditions.presence_of_element_located((By.ID, "txtResponse"))
            )
        except Exception:
            driver.switch_to.default_content()

        if not input_box:
            for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
                try:
                    driver.switch_to.frame(iframe)
                    input_box = WebDriverWait(driver, 5).until(
                        expected_conditions.presence_of_element_located((By.ID, "txtResponse"))
                    )
                    break
                except Exception:
                    driver.switch_to.default_content()

        if not input_box:
            # Check for credential error before reporting generic failure
            error_text = ""
            for el in driver.find_elements(By.CSS_SELECTOR, ".login_message, [id*='error'], [class*='error']"):
                t = el.text.strip()
                if t:
                    error_text = t
                    break
            detail = f" ({error_text})" if error_text else ""
            raise Exception(f"Could not locate 2FA input field{detail} — check credentials or app.log")

        # Generate token at the last moment for maximum TOTP validity window
        token = generate_totp_token(totp_secret)
        print(f"Generated 2FA token: {token}")

        set_progress(progress_session_id, "Entering 2FA token...", 5, 8)

        input_box.send_keys(token)
        time.sleep(0.5)
        input_box.send_keys(Keys.ENTER)
        driver.switch_to.default_content()

        set_progress(progress_session_id, "Waiting for login to complete...", 6, 8)

        ready = WebDriverWait(driver, 30).until(expected_conditions.url_to_be("https://nunavutprod.service-now.com/sp"))

        set_progress(progress_session_id, "Login successful! Preparing sessions...", 7, 8)

        # ---------------------------------------------------------------
        # Refresh site cache if stale, then pre-compute all site matches
        # ---------------------------------------------------------------
        live_sites, cache_stale = load_site_cache()
        if cache_stale:
            set_progress(progress_session_id, "Refreshing site list from ServiceNow (cache expired)...", 7, 8)
            try:
                driver.get("https://nunavutprod.service-now.com/sp/?id=sc_cat_item&sys_id=35083704dbe305908e611bad139619a5")
                WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable((By.ID, "s2id_sp_formfield_select_sites"))
                )
                fresh = get_all_dropdown_options_from_html(driver, "s2id_sp_formfield_select_sites")
                if fresh:
                    save_site_cache(fresh)
                    live_sites = fresh
                    set_progress(progress_session_id, f"Site list updated: {len(live_sites)} sites cached.", 7, 8)
            except Exception as e:
                print(f"[site_cache] Could not refresh: {e}")

        set_progress(progress_session_id, "Matching sessions to sites...", 7, 8)
        precomputed = {}
        needs_gpt = []
        for s in book_sessions:
            m = basic_site_match(s.community.strip(), s.school.strip(), s.building.strip(),
                                 all_site_options=live_sites)
            if m:
                precomputed[s.s_id] = m
            else:
                needs_gpt.append(s)

        if needs_gpt and chatgpt_api_key:
            set_progress(progress_session_id,
                         f"Running ChatGPT matching for {len(needs_gpt)} session(s)...", 7, 8)
            def _gpt_match(s):
                community_lower = s.community.strip().lower()
                filtered = [x for x in live_sites if community_lower in x.lower()]
                opts = filtered if filtered else live_sites
                return s.s_id, ask_chatgpt_for_best_match(opts, s.community, s.school, chatgpt_api_key)
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as gpt_pool:
                for sid, match in gpt_pool.map(_gpt_match, needs_gpt):
                    if match:
                        precomputed[sid] = match

        set_progress(progress_session_id,
                     f"Site matching: {len(precomputed)}/{len(book_sessions)} matched. Starting booking...", 7, 8)

        # Track results with more detail
        successful_sessions = []
        failed_sessions = []

        # Process each session
        for cn_session in book_sessions:
            current_session += 1
            session_progress_msg = f"Processing session {current_session}/{total_sessions}: {cn_session.title}"
            set_progress(progress_session_id, session_progress_msg, 8, 8, "running",
                         session_ref=cn_session.s_id)

            print("Processing", cn_session.title, "at", cn_session.school)

            try:
                # Verify Zoom meeting exists in Airtable
                zoom_check_result = check_zoom_meeting(cn_session, airtable_api_key)

                if not zoom_check_result:
                    set_progress(progress_session_id, "No Zoom link found", 8, 8, "zoom_warning",
                                 session_ref=cn_session.s_id)
                    raise Exception("No Zoom link found — session skipped")
                else:
                    set_progress(progress_session_id, "Zoom confirmed", 8, 8, "zoom_ok",
                                 session_ref=cn_session.s_id)

                # Submit GN ticket
                ticket_result = do_gn_ticket(
                    driver,
                    cn_session,
                    username,
                    pw,
                    buffer_before,
                    buffer_after,
                    progress_session_id,
                    airtable_api_key,
                    chatgpt_api_key,
                    allow_manual_site_selection,
                    headless_mode,
                    precomputed_site=precomputed.get(cn_session.s_id),
                    live_sites=live_sites,
                )

                # Mark as successfully requested in Airtable
                set_airtable_field(cn_session, "GN Ticket Requested", True, airtable_api_key)

                ticket_id = ticket_result.get('ticket_id', 'Unknown')
                successful_sessions.append({
                    'session_id': cn_session.s_id,
                    'title': cn_session.title,
                    'school': cn_session.school,
                    'teacher': cn_session.teacher,
                    'start_time': cn_session.start_time.isoformat() if cn_session.start_time else None,
                    'length': cn_session.length,
                    'ticket_id': ticket_id,
                })

            except Exception as e:
                error_msg = f"❌ Error processing {cn_session.title}: {str(e)}"
                failed_sessions.append({
                    'title': cn_session.title,
                    'error': str(e)
                })
                set_progress(progress_session_id, str(e), 8, 8, "session-failed",
                             session_ref=cn_session.s_id)
                print(f"Error processing {cn_session.title}: {repr(e)}")

        # Create detailed completion message
        total_processed = len(successful_sessions) + len(failed_sessions)
        completion_msg = f"Processing complete! {len(successful_sessions)} successful"

        if failed_sessions:
            failed_titles = [s['title'] for s in failed_sessions]
            completion_msg += f", {len(failed_sessions)} failed ({', '.join(failed_titles)})"

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
            time.sleep(3)
        driver.quit()

    return {
        'successful_sessions': successful_sessions,
        'failed_sessions': failed_sessions,
        'warning_sessions': [],
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


# The site matcher's model, overridable without a code change — OpenAI retires models
# on a schedule (gpt-3.5-turbo shuts down 2026-10-23), and a hardcoded ID is how that
# becomes an outage. Confirm the current ID against OpenAI's model list before changing.
CHATGPT_MODEL = os.getenv("CHATGPT_MODEL", "gpt-5-mini")
CHATGPT_TIMEOUT = int(os.getenv("CHATGPT_TIMEOUT", "30"))


def _chat_completion_payload(model, prompt, max_output_tokens=100):
    """Build a request body valid for the given model family.

    The GPT-5 family rejects `temperature` outright and renamed `max_tokens` to
    `max_completion_tokens`, so a bare model-string swap from a GPT-4-era model
    returns 400. Branching here keeps CHATGPT_MODEL genuinely swappable.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }

    if model.startswith("gpt-5") or model.startswith("o1") or model.startswith("o3"):
        payload["max_completion_tokens"] = max_output_tokens
    else:
        payload["max_tokens"] = max_output_tokens
        # Near-deterministic: this is a lookup, not a creative task.
        payload["temperature"] = 0.1

    return payload


def ask_chatgpt_for_best_match(dropdown_options, community_name, school_name, api_key=None):
    """Pick the ServiceNow site that matches a school, when string matching could not.

    This is a fallback behind basic_site_match(). Every answer is checked against the
    options actually offered, so a wrong or invented answer is discarded rather than
    submitted — the caller falls back to manual site selection.

    Returns the exact option text, or None.
    """
    if not dropdown_options or not api_key:
        set_progress_func(None, "DEBUG SITE: Site matching by model skipped (no options or API key).", None, None)
        return None

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
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=_chat_completion_payload(CHATGPT_MODEL, prompt),
            timeout=CHATGPT_TIMEOUT,
        )

        if response.status_code != 200:
            # Logged, not just printed: on the scheduled run nobody is watching stdout,
            # and a retired model ID surfaces here as a 404 rather than as bad matching.
            logging.warning("Site matching model %s returned HTTP %s: %s",
                            CHATGPT_MODEL, response.status_code, response.text[:300])
            set_progress_func(None, f"DEBUG SITE: Site matching API error: {response.status_code}",
                              None, None, "error")
            return None

        suggested_match = response.json()["choices"][0]["message"]["content"].strip()

        for option in dropdown_options:
            if option.strip().lower() == suggested_match.lower():
                set_progress_func(None, f"DEBUG SITE: Model suggested valid option: '{option}'.", None, None)
                return option

        logging.info("Site matching model suggested %r, which is not an available option.", suggested_match)
        set_progress_func(None,
                          f"DEBUG SITE: Model suggested '{suggested_match}' but it's not in available options.",
                          None, None, "warning")
        return None

    except Exception as e:
        logging.warning("Site matching call failed: %s", e, exc_info=True)
        set_progress_func(None, f"DEBUG SITE: Error calling site matching API: {e}", None, None, "error")
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

    Uses reduced sleep times and retries up to 3 times (1 s apart) before
    giving up, so that a briefly-slow page doesn't fail a session.
    """
    _SCROLL_S   = 0.5   # after scrollIntoView
    _CLICK_S    = 0.8   # after opening the dropdown
    _FILTER_S   = 1.5   # after typing the search text
    _SELECT_S   = 0.5   # after pressing ENTER
    _RETRY_WAIT = 1.0
    _MAX_RETRIES = 3

    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            dropdown_container = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, element_id))
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                dropdown_container,
            )
            time.sleep(_SCROLL_S)

            try:
                dropdown_container.click()
            except (ElementClickInterceptedException, StaleElementReferenceException):
                driver.execute_script("arguments[0].click();", dropdown_container)
            time.sleep(_CLICK_S)

            active_element = driver.switch_to.active_element
            active_element.send_keys(text)
            time.sleep(_FILTER_S)
            active_element.send_keys(Keys.ENTER)
            time.sleep(_SELECT_S)
            return True

        except Exception as e:
            last_exc = e
            # Close any open dropdown before retry
            try:
                driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            except Exception:
                pass
            if attempt < _MAX_RETRIES:
                print(f"Dropdown attempt {attempt + 1} failed for '{text}' in {element_id}: {e}. Retrying...")
                time.sleep(_RETRY_WAIT)
            else:
                set_progress_func(
                    None,
                    f"DEBUG SITE: Dropdown selection failed for '{text}' in {element_id}: {last_exc}",
                    None, None, "error",
                )
                print(f"Dropdown selection failed for '{text}': {last_exc}")
                return False
    return False


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


def basic_site_match(community, school, building, all_site_options=AVAILABLE_SITES):
    """Quickly match site using simple heuristics before invoking ChatGPT."""
    candidates = []
    building_first = get_first_word(building)
    school_first = get_first_word(school)

    candidates.append(f"{community} {building_first}".strip())
    candidates.append(f"{community} {school_first}".strip())
    candidates.extend([community, building_first, school_first])

    # remove duplicates and empties while preserving order
    seen = set()
    cleaned = []
    for c in candidates:
        if c and c not in seen:
            cleaned.append(c)
            seen.add(c)

    lower_map = {s.lower(): s for s in all_site_options}

    def tokenize(text):
        return [w.strip('.,;:!?-') for w in text.lower().split() if w.strip('.,;:!?-')]

    community_tokens = set(tokenize(community))
    ref_tokens = set(tokenize(school)) | set(tokenize(building))
    # remove tokens similar to the community to focus on school/building descriptors
    filtered_tokens = set()
    for t in ref_tokens:
        if not any(difflib.SequenceMatcher(None, t, c).ratio() >= 0.8 for c in community_tokens):
            filtered_tokens.add(t)

    def score_site(site):
        site_tokens = set(tokenize(site))
        return len(site_tokens & filtered_tokens)

    # Exact match
    for cand in cleaned:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]

    # Substring match with backward scoring
    for cand in cleaned:
        matches = [site for site in all_site_options if cand.lower() in site.lower()]
        if matches:
            if len(matches) == 1:
                return matches[0]
            # choose the match with highest overlap with school/building tokens
            best = max(matches, key=score_site)
            if score_site(best) > 0:
                return best
            return matches[0]

    # Fuzzy match
    site_lowers = list(lower_map.keys())
    for cand in cleaned:
        matches = difflib.get_close_matches(cand.lower(), site_lowers, n=1, cutoff=0.85)
        if matches:
            return lower_map[matches[0]]

    return None


def smart_site_selection(driver, cn_session, wait_time=1.5, progress_session_id=None, chatgpt_api_key=None,
                         allow_manual_selection=False, headless_mode=True, precomputed_site=None,
                         live_sites=None):
    """Intelligently select the site field using quick heuristics and ChatGPT fallback.

    If precomputed_site is provided it is tried first, skipping heuristic/ChatGPT lookup.
    live_sites, if provided, is used instead of the global AVAILABLE_SITES for matching.
    """
    element_id = "s2id_sp_formfield_select_sites"
    site_options = live_sites if live_sites else AVAILABLE_SITES

    # Use pre-computed match if available
    if precomputed_site:
        set_progress_func(progress_session_id,
                          f"DEBUG SITE: Using pre-computed match '{precomputed_site}'.", None, None)
        if try_dropdown_selection(driver, element_id, precomputed_site, wait_time):
            return precomputed_site   # return the matched name so caller can surface it
        set_progress_func(progress_session_id,
                          f"DEBUG SITE: Pre-computed match failed in browser, falling back.", None, None, "warning")

    set_progress_func(progress_session_id,
                      f"DEBUG SITE: Starting smart site selection for '{cn_session.school}' in '{cn_session.community}'",
                      None, None)

    quick_match = basic_site_match(cn_session.community.strip(), cn_session.school.strip(),
                                   cn_session.building.strip(), all_site_options=site_options)
    if quick_match:
        set_progress_func(progress_session_id,
                          f"DEBUG SITE: Quick match found '{quick_match}'. Attempting selection...", None, None)
        if try_dropdown_selection(driver, element_id, quick_match, wait_time):
            set_progress_func(progress_session_id,
                              f"DEBUG SITE: Successfully selected '{quick_match}'.", None, None)
            return quick_match
        else:
            set_progress_func(progress_session_id,
                              f"DEBUG SITE: Failed to select quick match '{quick_match}'.", None, None, "warning")
    else:
        set_progress_func(progress_session_id,
                          "DEBUG SITE: No quick match found. Proceeding to ChatGPT...", None, None)

    if chatgpt_api_key:
        community_lower = cn_session.community.strip().lower()
        community_filtered = [s for s in site_options if community_lower in s.lower()]
        target_options = community_filtered if community_filtered else site_options
        best_match = ask_chatgpt_for_best_match(target_options, cn_session.community, cn_session.school, chatgpt_api_key)
        if best_match:
            set_progress_func(progress_session_id,
                              f"DEBUG SITE: ChatGPT suggested '{best_match}'. Attempting selection...", None, None)
            if try_dropdown_selection(driver, element_id, best_match, wait_time):
                set_progress_func(progress_session_id,
                                  f"DEBUG SITE: Successfully selected ChatGPT's suggestion: '{best_match}'.", None, None)
                return best_match
            else:
                set_progress_func(progress_session_id,
                                  f"DEBUG SITE: Failed to select ChatGPT's suggestion: '{best_match}'.", None, None, "warning")
        else:
            set_progress_func(progress_session_id,
                              "DEBUG SITE: ChatGPT could not determine a match.", None, None, "warning")
    else:
        set_progress_func(progress_session_id,
                          "DEBUG SITE: ChatGPT API key not configured. Skipping ChatGPT.", None, None, "warning")

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
            return manual_site_input
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


def do_gn_ticket(driver, cn_session, username, pw, buffer_before=10, buffer_after=10,
                 progress_session_id=None, api_key=None, chatgpt_api_key=None,
                 allow_manual_site_selection=False, headless_mode=True,
                 precomputed_site=None, live_sites=None):
    """Fill and submit the GN ticket form for a single session."""
    wait_time = 1.5
    s_ref = cn_session.s_id   # shorthand for session_ref

    set_progress(progress_session_id, f"Loading GN ticket form for {cn_session.title}...", None, None,
                 session_ref=s_ref)

    try:
        driver.get("https://nunavutprod.service-now.com/sp/?id=sc_cat_item&sys_id=35083704dbe305908e611bad139619a5")
    except Exception:
        try:
            Alert(driver).accept()
        except Exception:
            pass
    # Wait for the first dropdown to be ready instead of a bare sleep
    WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.ID, "s2id_sp_formfield_select_your_department"))
    )

    gn_form = driver.find_element(By.TAG_NAME, "body")

    set_progress(progress_session_id, f"Filling form fields for {cn_session.title}...", None, None)

    # Clear any leftover global active element state before starting form fill
    driver.switch_to.active_element.send_keys(Keys.ESCAPE)
    time.sleep(0.5)

    # Select your Department
    set_progress(progress_session_id, "Setting department to Connected North...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_select_your_department",
                                  "Connected North", wait_time):
        raise Exception("Failed to set department")

    # Department User
    set_progress(progress_session_id, "Setting department user to Education...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_department_user",
                                  "Education", wait_time):
        raise Exception("Failed to set department user")

    # Community
    set_progress(progress_session_id, f"Setting community: {cn_session.community}...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_community_video",
                                  cn_session.community, wait_time):
        raise Exception("Failed to set community")

    # Office Phone
    set_progress(progress_session_id, f"Setting phone number to {cn_session.phone}...", None, None)
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
    set_progress(progress_session_id,
                 f"Setting client name to {cn_session.teacher} at {cn_session.school}...",
                 None, None)
    client_name = driver.find_element(By.ID, "sp_formfield_client_name")
    driver.execute_script("arguments[0].scrollIntoView(true);", client_name)  # Scroll to element before interacting
    client_name.clear()
    client_name.send_keys(cn_session.teacher + " at " + cn_session.school)

    # Session topic
    set_progress(progress_session_id, f"Setting session topic to {cn_session.title}...", None, None)
    session_topic = driver.find_element(By.ID, "sp_formfield_session_topic_or_description")
    driver.execute_script("arguments[0].scrollIntoView(true);", session_topic)
    session_topic.clear()
    session_topic.send_keys(cn_session.title)

    # Screen layout
    set_progress(progress_session_id, "Setting screen layout to Full...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_screen_layout",
                                  "Full", wait_time):
        raise Exception("Failed to set screen layout")

    # Session date and time
    formatted_date = cn_session.start_time.strftime("%Y-%m-%d")
    EST = timezone('US/Eastern')
    start_time_EST = cn_session.start_time.astimezone(EST) - timedelta(minutes=buffer_before)
    end_time_EST = cn_session.start_time.astimezone(EST) + timedelta(minutes=cn_session.length + buffer_after)
    start_str = start_time_EST.strftime("%-I:%M %p")
    end_str = end_time_EST.strftime("%-I:%M %p")
    set_progress(
        progress_session_id,
        f"Setting session date and time to {formatted_date} from {start_str} to {end_str} EST...",
        None,
        None,
    )
    session_date = driver.find_element(By.ID, "sp_formfield_session_date")
    driver.execute_script("arguments[0].scrollIntoView(true);", session_date)
    session_date.click()
    element = driver.switch_to.active_element
    element.send_keys(formatted_date)
    time.sleep(wait_time)
    element.send_keys(Keys.ENTER)

    # Session start time HH:MM AM
    gn_form.send_keys(Keys.TAB)
    gn_form.send_keys(Keys.TAB)
    element = driver.switch_to.active_element
    element.send_keys(start_str)

    # Session end time HH:MM AM
    gn_form.send_keys(Keys.TAB)
    element = driver.switch_to.active_element
    element.send_keys(end_str)
    time.sleep(wait_time)

    # Time zone (always set to Eastern)
    set_progress(progress_session_id, "Setting timezone to Eastern...", None, None)
    if not try_dropdown_selection(driver, "s2id_sp_formfield_time_zone",
                                  "Eastern", wait_time):
        raise Exception("Failed to set timezone")

    # Site - Use smart selection with ChatGPT fallback, and potentially manual intervention
    set_progress(
        progress_session_id,
        f"Setting site information for {cn_session.title}...",
        None, None, session_ref=s_ref,
    )
    matched_site = smart_site_selection(
        driver, cn_session, wait_time, progress_session_id, chatgpt_api_key,
        allow_manual_site_selection, headless_mode,
        precomputed_site=precomputed_site, live_sites=live_sites,
    )
    if not matched_site:
        raise Exception(
            f"Failed to set site for {cn_session.title} - all selection methods failed (including manual if enabled).")
    set_progress(progress_session_id, f"Site: {matched_site}", None, None,
                 status="site_found", session_ref=s_ref)

    # Connection Details
    set_progress(progress_session_id, f"Setting connection details for {cn_session.title}...", None, None)
    conn_details = driver.find_element(By.ID, "sp_formfield_connection_details")
    driver.execute_script("arguments[0].scrollIntoView(true);", conn_details)  # Scroll to element before interacting
    conn_details.click()
    element = driver.switch_to.active_element
    element.clear()

    zoom_digits = get_zoom_digits(cn_session, api_key)
    if not zoom_digits:
        raise Exception("No Zoom connection details found — session skipped")
    element.send_keys(zoom_digits + "@zoomcrc.com")

    time.sleep(wait_time)
    element.send_keys(Keys.ENTER)
    time.sleep(wait_time)

    # Submit
    set_progress(progress_session_id, f"Submitting ticket for {cn_session.title}...", None, None)
    submit_btn = driver.find_element(By.ID, "submit-btn")
    driver.execute_script("arguments[0].scrollIntoView(true);", submit_btn)
    submit_btn.click()
    time.sleep(0.5)   # brief pause then let WebDriverWait handle the redirect

    WebDriverWait(driver, 30).until(expected_conditions.url_contains("&table=sc_request"))

    set_airtable_field(cn_session, "GN Ticket Requested", True, api_key)

    ticket_id = ""
    try:
        req_number_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'text-muted') and contains(.,'Request Number')]//b"))
        )
        ticket_id = req_number_element.text.strip()
        print("req number:", ticket_id)
        current_gn_ticket_id = cn_session.gn_ticket_id if cn_session.gn_ticket_id else ""
        set_airtable_field(cn_session, "GN Ticket ID", f"{current_gn_ticket_id} #gn-submitted {ticket_id}".strip(), api_key)
    except Exception as e:
        print(f"Could not retrieve ticket ID: {e}")
        ticket_id = "Unknown"

    set_progress(progress_session_id, ticket_id or "submitted", None, None,
                 status="session-complete", session_ref=s_ref)

    return {"status": "success", "ticket_id": ticket_id}


# Function to be called by main.py to set the progress function
def set_progress_callback(progress_func):
    global set_progress_func
    set_progress_func = progress_func