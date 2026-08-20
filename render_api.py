"""Triggering the scheduled scan on demand.

The web service cannot do the booking itself — it drives Chrome, and a small
instance cannot survive that. The cron job already has the memory for it, so
"run now" asks Render to start that job immediately rather than doing the work
here.
"""
import logging
import os

import requests

RENDER_API_BASE = os.getenv("RENDER_API_BASE", "https://api.render.com/v1")
REQUEST_TIMEOUT = int(os.getenv("RENDER_API_TIMEOUT", "15"))

logger = logging.getLogger(__name__)


def is_configured():
    return bool(os.getenv("RENDER_API_KEY") and os.getenv("RENDER_CRON_JOB_ID"))


def trigger_scan_job():
    """Start the scan cron job now.

    Returns (ok, message). The message is shown to the person who pressed the
    button, so it says what happened rather than what went wrong internally.
    """
    api_key = os.getenv("RENDER_API_KEY")
    job_id = os.getenv("RENDER_CRON_JOB_ID")

    if not api_key or not job_id:
        return False, ("On-demand runs are not configured on this deployment. "
                       "Your sessions will still be booked at the next scheduled check.")

    try:
        response = requests.post(
            f"{RENDER_API_BASE}/cron-jobs/{job_id}/runs",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:
        logger.error("Could not reach Render to trigger the scan job: %s", exc)
        return False, ("Could not start a run just now. Your request is saved and will "
                       "be picked up at the next scheduled check.")

    if response.status_code in (200, 201, 202):
        return True, "Scan started. You'll get an email when it finishes."

    logger.error("Render refused the cron trigger: HTTP %s %s",
                 response.status_code, response.text[:300])
    return False, ("Could not start a run just now. Your request is saved and will "
                   "be picked up at the next scheduled check.")
