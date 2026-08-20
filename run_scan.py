#!/usr/bin/env python
"""Entrypoint for the scheduled scan.

This is what the Render Cron Job runs. It does the same work the Celery beat +
worker pair did, but in a single short-lived process with no broker, so the
deployment needs neither Redis nor a long-running worker.

    python run_scan.py                  # scan and book for everyone who is due
    python run_scan.py --force          # ignore intervals, scan every opted-in user
    python run_scan.py --dry-run        # everything except submitting to ServiceNow
    python run_scan.py --user a@b.org   # one user, regardless of interval (repeatable)
    python run_scan.py --list-users     # show who is opted in, do nothing else

Exit codes: 0 success, 1 one or more users failed, 2 misconfiguration.
"""
import argparse
import logging
import os
import sys


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Run the GN Ticket Automator scheduled scan.")
    parser.add_argument("--user", action="append", dest="users", metavar="EMAIL",
                        help="Only scan this user. Repeatable. Defaults to every opted-in user.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Do everything except submit tickets to ServiceNow.")
    parser.add_argument("--list-users", action="store_true",
                        help="Print the opted-in users and exit without scanning.")
    parser.add_argument("--force", action="store_true",
                        help="Scan every opted-in user now, ignoring their chosen interval.")
    parser.add_argument("--max-bookings", type=int, default=None, metavar="N",
                        help="Cap bookings per user per run. 0 means no cap.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # These are read at import time by tasks.py, so they must be set before it loads.
    os.environ["GN_INLINE_TASKS"] = "1"
    if args.dry_run:
        os.environ["GN_DRY_RUN"] = "1"
    if args.max_bookings is not None:
        os.environ["GN_MAX_BOOKINGS_PER_RUN"] = str(args.max_bookings)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_scan")

    if not os.getenv("APP_ENCRYPTION_KEY"):
        log.error("APP_ENCRYPTION_KEY is not set. Stored credentials cannot be decrypted.")
        return 2

    try:
        import tasks
        from user_profiles import user_manager
    except Exception:
        log.exception("Could not start: import failed.")
        return 2

    if tasks.DRY_RUN:
        log.warning("DRY RUN: no tickets will be submitted to ServiceNow.")

    if args.list_users:
        for email in user_manager.list_auto_enabled_users():
            print(email)
        return 0

    if args.users:
        # Naming someone explicitly is a request to scan them, interval or not.
        emails = args.users
    else:
        opted_in = user_manager.list_auto_enabled_users()
        if not opted_in:
            log.info("No users have auto-booking enabled. Nothing to do.")
            return 0

        emails = opted_in if args.force else [e for e in opted_in if tasks.user_is_due(e)]
        if not emails:
            log.info("%d user(s) opted in, none due yet. Nothing to do.", len(opted_in))
            return 0

    failures = 0
    for email in emails:
        try:
            log.info("Scanning %s", email)
            tasks.dispatch_scan(email)
        except Exception:
            failures += 1
            log.exception("Scan failed for %s", email)

    log.info("Run complete: %d user(s), %d failure(s).", len(emails), failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
