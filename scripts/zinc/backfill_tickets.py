#!/usr/bin/env python3
"""
backfill_tickets.py — load older history into the dashboard.

Three steps, in the order that matters:

  1. Raise the org's Sync Days setting, which is what governs how far back a
     sync reaches when there is no watermark. Shared with the pull request
     sync, so this widens both.
  2. Rewind every watermark to the same date. Without this, step 1 changes
     nothing: once a sync has run, the watermark wins.
  3. Trigger a sync and wait.

Every step goes through Middleware's own endpoints. Nothing here touches the
database, so there is nothing to undo badly.

    python3 backfill_tickets.py --months 12 --dry-run
    python3 backfill_tickets.py --months 12
    python3 backfill_tickets.py --months 24 --skip-sync

Standard library only.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DEFAULT_BASE_URL = "http://localhost:3333"
ANALYTICS_URL = "http://localhost:9696"

# The Settings page refuses anything above this, and the ticket API's own
# window is set just above it, so this is the practical ceiling.
MAX_SYNC_DAYS = 366


def http(url, method="GET", data=None, timeout=300):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-backfill-tickets")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError("HTTP {} {} {}\n{}".format(err.code, method, url, detail))
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach {}: {}".format(url, err.reason))


def main():
    parser = argparse.ArgumentParser(
        description="Load older history into the Middleware dashboard."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--analytics-url", default=ANALYTICS_URL)
    parser.add_argument(
        "--months",
        type=int,
        default=12,
        help="How far back to load. For Zinc: 12 months is about 2,100 "
             "stories, 24 months about 3,200.",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Set the window and rewind, but do not trigger the sync.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # 30.44, not 31: at 31 days a month, "12 months" came out as 372 days and
    # tripped the 366-day ceiling warning for what is plainly a year.
    days = round(args.months * 30.44)
    if days > MAX_SYNC_DAYS:
        print(
            "Note: {} months is {} days, above the {}-day ceiling the "
            "Settings page allows.".format(args.months, days, MAX_SYNC_DAYS)
        )
        print(
            "      The setting will be capped at {}, but the watermark rewind "
            "below".format(MAX_SYNC_DAYS)
        )
        print("      takes an exact date and is not capped, so the sync will")
        print("      still reach back the full {} months.".format(args.months))
        print("")

    base = args.base_url.rstrip("/")
    analytics = args.analytics_url.rstrip("/")
    since = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        session = http(base + "/api/auth/session")
    except RuntimeError as err:
        sys.exit(
            "{}\n\nMiddleware does not seem to be up at {}. Start it with:\n"
            "    docker compose watch".format(str(err).splitlines()[0], base)
        )
    org_id = (session.get("org") or {}).get("id")
    if not org_id:
        sys.exit("Middleware answered but returned no org id.")

    print("=== Plan ===")
    print("  org              {}".format(org_id))
    print("  load back to     {}  ({} months)".format(since.date(), args.months))
    print("  sync days -> {:<4}  (capped at {})".format(
        min(days, MAX_SYNC_DAYS), MAX_SYNC_DAYS))
    print("  rewind watermarks to {}".format(since.isoformat()))
    print("  trigger sync     {}".format("no" if args.skip_sync else "yes"))
    print("")
    print("  This re-reads and refreshes; it does not duplicate. Tickets are")
    print("  upserted on their idempotency key and each ticket's state")
    print("  history is replaced from Shortcut, which is authoritative.")
    print("")

    if args.dry_run:
        print("Dry run — nothing sent. Drop --dry-run to apply.")
        return

    print("=== 1. Sync days setting ===")
    try:
        result = http(
            "{}/api/internal/{}/settings".format(base, org_id),
            method="PUT",
            data={
                "setting_type": "DEFAULT_SYNC_DAYS_SETTING",
                "setting_data": {"default_sync_days": min(days, MAX_SYNC_DAYS)},
            },
        )
        print("  set to {}".format(
            (result or {}).get("default_sync_days", min(days, MAX_SYNC_DAYS))))
    except RuntimeError as err:
        print("  {}".format(str(err).splitlines()[0]))
        print("")
        print("  The Settings page refuses a value lower than the current one.")
        print("  If that is what happened, you are already set wider than")
        print("  {} days and nothing needs changing here.".format(days))

    print("")
    print("=== 2. Rewind the watermarks ===")
    try:
        reset = http(
            "{}/orgs/{}/bookmark/reset?{}".format(
                analytics,
                org_id,
                urllib.parse.urlencode({"bookmark_timestamp": since.isoformat()}),
            ),
            method="PUT",
        )
    except RuntimeError as err:
        sys.exit(
            "{}\n\nThe analytics server is on port 9696 and is not proxied "
            "through 3333, so this step talks to it directly. If you are "
            "running Middleware in Docker, that port is published by "
            "default.".format(str(err).splitlines()[0])
        )

    print("  repo, incident, workflow and merge-to-deploy -> {}".format(
        reset.get("updated_bookmark")))
    providers = reset.get("ticket_providers_reset")
    if providers is None:
        print("")
        print("  WARNING: the response carries no `ticket_providers_reset`,")
        print("  which means the installed bookmark route predates Layer 5 and")
        print("  tickets have NOT been rewound. Pull requests will backfill;")
        print("  tickets will not. Install Layer 5 and re-run:")
        print("      python3 apply_cockpit_layer5.py --repo ~/Downloads/middleware")
        sys.exit(1)
    print("  tickets -> {}".format(", ".join(providers) or "none"))

    if args.skip_sync:
        print("")
        print("Watermarks rewound. The next scheduled sync will load the")
        print("history, or trigger one now with:")
        print("  curl -X POST http://localhost:9697/sync")
        return

    print("")
    print("=== 3. Sync ===")
    print("  This takes a while. For 12 months of Zinc's Shortcut data that is")
    print("  about 2,100 stories, one history call each, against a")
    print("  170-per-minute self-imposed throttle — roughly 13 minutes for the")
    print("  tickets alone (20 for 24 months), plus the pull request backfill.")
    print("")
    try:
        http("{}/api/internal/{}/sync_repos".format(base, org_id), method="POST",
             data={}, timeout=600)
        print("  sync requested")
    except RuntimeError as err:
        print("  {}".format(str(err).splitlines()[0]))
        print("  Trigger it by hand instead:  curl -X POST http://localhost:9697/sync")

    print("")
    print("Watch it land:")
    print("  docker compose logs -f | grep -iE 'Tickets Sync|Shortcut Sync'")
    print("")
    print("Then check the numbers moved:")
    print("  python3 ../cockpit-layer2/verify_ticket_flow.py --team skipper --days 365")
    print("")
    print("Note the Cockpit view allows a window up to 400 days; the DORA")
    print("pages are still capped at 105 by upstream Middleware.")


if __name__ == "__main__":
    main()
