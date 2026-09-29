#!/usr/bin/env python3
"""
verify_ticket_flow.py — are the Layer 2 numbers right?

Calls the new endpoint, prints the flow metrics in plain English, and then
asks Shortcut the same question directly so the two can be compared. A number
that Middleware produces and Shortcut disagrees with is worth knowing about
before anyone puts it on a slide.

Read-only. SHORTCUT_TOKEN is read from the environment, sent only to
Shortcut, and never written or printed.

    export SHORTCUT_TOKEN=...
    python3 verify_ticket_flow.py
    python3 verify_ticket_flow.py --team skipper --days 90

Standard library only.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DEFAULT_BASE_URL = "http://localhost:3333"
SHORTCUT_API = "https://api.app.shortcut.com/api/v3"


def http(url, headers=None, timeout=180):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-verify-ticket-flow")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError("HTTP {} for {}\n{}".format(err.code, url, detail))
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach {}: {}".format(url, err.reason))


def humanise(seconds):
    """Seconds as something a person can read."""
    if seconds is None:
        return "—"
    if seconds < 3600:
        return "{}m".format(round(seconds / 60))
    if seconds < 86400:
        return "{}h".format(round(seconds / 3600, 1))
    return "{}d".format(round(seconds / 86400, 1))


def print_durations(label, stats, width=18):
    if not stats or not stats.get("count"):
        print("  {:<{w}} no data".format(label, w=width))
        return
    print(
        "  {:<{w}} p50 {:>7}   p75 {:>7}   p95 {:>7}   (n={})".format(
            label,
            humanise(stats.get("p50")),
            humanise(stats.get("p75")),
            humanise(stats.get("p95")),
            stats.get("count"),
            w=width,
        )
    )


def shortcut_completed_count(headers, mention_name, since, until):
    query = "team:{} completed:{}..{}".format(
        mention_name, since.strftime("%Y-%m-%d"), until.strftime("%Y-%m-%d")
    )
    url = "{}/search/stories?{}".format(
        SHORTCUT_API, urllib.parse.urlencode({"query": query, "page_size": 1})
    )
    return http(url, headers=headers).get("total")


def main():
    parser = argparse.ArgumentParser(
        description="Check the ticket flow metrics against Shortcut."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--team", default="skipper", help="GitHub team slug")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument(
        "--skip-shortcut",
        action="store_true",
        help="Do not call Shortcut; just print what Middleware says.",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days)

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

    url = "{}/api/internal/{}/ticket_flow?{}".format(
        base,
        org_id,
        urllib.parse.urlencode(
            {
                "from_date": since.date().isoformat(),
                "to_date": until.date().isoformat(),
                "github_team": args.team,
            }
        ),
    )

    try:
        data = http(url)
    except RuntimeError as err:
        print(str(err))
        print("")
        print("If this is a 404, the new route has not been picked up. Check")
        print("that docker compose watch is running (not docker compose up),")
        print("and that the file landed at:")
        print("  web-server/pages/api/internal/[org_id]/ticket_flow.ts")
        print("")
        print("If this is a 500, the Flask blueprint is the likely cause:")
        print("  docker compose logs analytics_server | tail -40")
        sys.exit(1)

    if data.get("unmapped"):
        sys.exit(
            "GitHub team '{}' has no Shortcut team mapped to it.\n"
            "  python3 ../cockpit/setup_shortcut.py --list-teams\n"
            "  python3 ../cockpit/setup_shortcut.py --only {}".format(
                args.team, args.team
            )
        )

    print("=== {} — last {} days ===".format(args.team, args.days))
    print("  counts below are as of {}".format(data.get("as_of") or "—"))
    print("")
    print("  throughput         {} ticket(s) completed".format(data["throughput"]))
    print("  bug ratio          {}".format(
        "{}%".format(data["bug_ratio"]) if data["bug_ratio"] is not None else "—"
    ))
    print("  in progress now    {}".format(data["wip"]))
    print("  blocked now        {}".format(data["blocked_now"]))
    print("  open in total      {}".format(data["open_total"]))
    print("")

    print("Elapsed time")
    print_durations("lead time", data["lead_time"])
    print_durations("cycle time", data["cycle_time"])
    print_durations("waiting to start", data["queue_time"])
    print_durations("blocked", data["blocked_time"])
    print("")

    if data["state_metrics"]:
        print("Where the time goes")
        print("  {:<20} {:>10} {:>8} {:>8} {:>7}".format(
            "state", "p50", "tickets", "visits", "open"))
        for metric in data["state_metrics"][:10]:
            print("  {:<20} {:>10} {:>8} {:>8} {:>7}".format(
                metric["state"][:20],
                humanise(metric["duration"]["p50"]),
                metric["tickets"],
                metric["visits"],
                metric["open_tickets"],
            ))
        print("")
        print("  `visits` above `tickets` means work came back — a state with")
        print("  twice the visits of tickets is being re-entered every time.")
        print("")

    linkage = data["pull_request_linkage"]
    print("Ticket to pull request")
    print("  tickets with a linked PR   {} of {} ({})".format(
        linkage["tickets_with_link"], linkage["tickets"],
        "{}%".format(linkage["link_rate"]) if linkage["link_rate"] is not None else "—"))
    print("  links resolved to a PR row {} of {} ({})".format(
        linkage["links_resolved_to_pull_request"], linkage["links_total"],
        "{}%".format(linkage["resolution_rate"])
        if linkage["resolution_rate"] is not None else "—"))
    if linkage["links_total"] and (linkage["resolution_rate"] or 0) < 80:
        print("")
        print("  A low resolution rate means Shortcut recorded the PR but")
        print("  Middleware has not synced that repo, or two repos share the")
        print("  PR number. Unresolved links are left null rather than")
        print("  guessed, so this is safe — just incomplete.")
    print("")

    quality = data["data_quality"]
    print("Data quality")
    print("  completed without ever being started   {}".format(
        quality["completed_without_start"]))
    print("  completed with no state history        {}".format(
        quality["completed_without_transitions"]))
    print("  open, contributing nothing to states   {}".format(
        quality["active_without_state_history"]))
    print("  contradictory provider timestamps      {}".format(
        quality["inconsistent_timestamps"]))
    print("  open with an unclassifiable state      {}".format(
        quality["open_state_unknown"]))
    print("  completed with no estimate             {}".format(
        quality["unestimated_completed"]))
    if quality["tickets_by_workflow"]:
        print("  by workflow:")
        for workflow_id, count in quality["tickets_by_workflow"].items():
            note = ""
            # 500000529 is "Mid-Longterm themes" — theme records, not
            # delivery work. Its presence skews throughput.
            if workflow_id == "500000529":
                note = "   <- Mid-Longterm themes, not delivery work"
            print("    {:<14} {}{}".format(workflow_id, count, note))
    if quality["completed_without_start"]:
        print("")
        print("  Tickets closed without being started have no cycle time, so")
        print("  they are excluded from that figure rather than counted as")
        print("  zero. If that number is large, cycle time describes a subset.")
    print("")

    if data["epics"]:
        print("Epics")
        for epic in data["epics"][:8]:
            print("  {:<40} {:>3}/{:<3} {:>6}   ({} blocked)".format(
                epic["epic_id"][:40], epic["completed"], epic["total"],
                "{}%".format(epic["percent_complete"])
                if epic["percent_complete"] is not None else "—",
                epic["blocked"]))
        print("")

    if not data["iterations"]:
        print("Iterations: none. Zinc has no Shortcut iterations, so sprint")
        print("progress stays empty until stories are assigned to one.")
        print("")

    if args.skip_shortcut:
        return

    token = os.environ.get("SHORTCUT_TOKEN", "").strip()
    if not token:
        print("SHORTCUT_TOKEN not set, so skipping the cross-check against")
        print("Shortcut. Set it to compare Middleware's throughput with")
        print("Shortcut's own count.")
        return

    print("=== Cross-check against Shortcut ===")
    try:
        expected = shortcut_completed_count(
            {"Shortcut-Token": token}, args.team, since, until
        )
    except RuntimeError as err:
        print("  Could not ask Shortcut: {}".format(str(err).splitlines()[0]))
        return

    print("  Shortcut says completed:   {}".format(expected))
    print("  Middleware says throughput: {}".format(data["throughput"]))

    if expected is None:
        return
    if expected == data["throughput"]:
        print("  Exact match.")
        return

    gap = expected - data["throughput"]
    print("")
    print("  Difference of {}. Expected reasons, in order of likelihood:".format(gap))
    print("   - Middleware excludes 'Cancelled', Shortcut's search does not")
    print("   - Middleware excludes sub-tasks; the parent is the unit of work")
    print("   - the sync's backfill window is shorter than --days")
    print("   - stories moved team after completion")
    print("")
    print("  To test the first two, ask for the excluded rows back:")
    print("    curl -s '{}&include_subtasks=true' | python3 -c \\".format(url))
    print("      'import json,sys; print(json.load(sys.stdin)[\"throughput\"])'")


if __name__ == "__main__":
    main()
