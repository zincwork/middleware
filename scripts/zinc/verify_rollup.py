#!/usr/bin/env python3
"""
verify_rollup.py — check the manager roll-up, and check the weighting.

Prints the per-team figures side by side, then the combined figure, then the
number you would have got by averaging the per-team medians. If those last
two differ — and with teams of different sizes they will — that difference is
the reason the roll-up is a separate query rather than a sum of the rows
above it.

Read-only. Needs no token: everything comes from Middleware.

    python3 verify_rollup.py --manager "Lucila Sanjurjo"
    python3 verify_rollup.py --teams skipper,red-team --days 90
    python3 verify_rollup.py --list-managers

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


def http(url, timeout=180):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-verify-rollup")
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
    if seconds is None:
        return "—"
    if seconds < 3600:
        return "{}m".format(round(seconds / 60))
    if seconds < 86400:
        return "{}h".format(round(seconds / 3600, 1))
    return "{}d".format(round(seconds / 86400, 1))


def main():
    parser = argparse.ArgumentParser(
        description="Check the multi-team and manager roll-up."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--manager", help='e.g. "Lucila Sanjurjo"')
    parser.add_argument("--teams", help="Comma-separated GitHub team slugs")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument(
        "--list-managers",
        action="store_true",
        help="Show the managers set in github_teams.json and stop.",
    )
    args = parser.parse_args()

    # Checked before reaching for the network, so a missing argument reports
    # the missing argument rather than a connection error.
    if not args.list_managers and not args.manager and not args.teams:
        sys.exit('Pass --manager "Name" or --teams a,b (or --list-managers).')

    base = args.base_url.rstrip("/")
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

    if args.list_managers:
        teams = http("{}/api/resources/github_teams".format(base))
        rows = teams if isinstance(teams, list) else teams.get("teams", [])
        by_manager = {}
        for team in rows:
            manager = (team.get("manager") or "").strip()
            by_manager.setdefault(manager or "(no manager)", []).append(team["slug"])
        print("=== Managers in github_teams.json ===")
        for manager, slugs in sorted(by_manager.items()):
            print("  {:<22} {}".format(manager, ", ".join(sorted(slugs))))
        print("")
        print("Set them with:")
        print("  python3 set_managers.py --config "
              "~/Downloads/middleware/web-server/config/github_teams.json")
        return

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days)
    query = {
        "from_date": since.date().isoformat(),
        "to_date": until.date().isoformat(),
    }
    if args.manager:
        query["manager"] = args.manager
    if args.teams:
        query["github_teams"] = args.teams

    url = "{}/api/internal/{}/ticket_flow_comparison?{}".format(
        base, org_id, urllib.parse.urlencode(query)
    )

    try:
        data = http(url)
    except RuntimeError as err:
        print(str(err))
        print("")
        print("A 404 means the new route has not been picked up — check that")
        print("docker compose watch is running, not docker compose up.")
        sys.exit(1)

    scope = data.get("scope") or {}
    if scope.get("kind") == "empty":
        sys.exit("Nothing in scope: {}".format(scope.get("reason")))

    label = (
        "manager {}".format(scope["manager"])
        if scope.get("kind") == "manager"
        else "teams {}".format(args.teams)
    )
    print("=== {} — last {} days ===".format(label, args.days))
    print("")

    rows = data.get("teams") or []
    print("  {:<16} {:>10} {:>10} {:>10} {:>8} {:>7}".format(
        "team", "throughput", "cycle p50", "lead p50", "bugs", "wip"))
    for row in rows:
        metrics = row["metrics"]
        if row["unmapped"]:
            print("  {:<16} {:>10}   (no Shortcut team mapped)".format(
                row["slug"], "—"))
            continue
        print("  {:<16} {:>10} {:>10} {:>10} {:>8} {:>7}".format(
            row["slug"],
            metrics["throughput"],
            humanise(metrics["cycle_time"]["p50"]),
            humanise(metrics["lead_time"]["p50"]),
            "{}%".format(metrics["bug_ratio"])
            if metrics["bug_ratio"] is not None else "—",
            metrics["wip"],
        ))

    combined = data.get("combined")
    if not combined:
        print("")
        print("No combined figure: no team in scope has a Shortcut team")
        print("mapped, and a zero here would read as a measurement.")
        return

    print("  " + "-" * 64)
    print("  {:<16} {:>10} {:>10} {:>10} {:>8} {:>7}".format(
        "COMBINED",
        combined["throughput"],
        humanise(combined["cycle_time"]["p50"]),
        humanise(combined["lead_time"]["p50"]),
        "{}%".format(combined["bug_ratio"])
        if combined["bug_ratio"] is not None else "—",
        combined["wip"],
    ))
    print("")

    if data.get("unmapped_teams"):
        print("  Not measured: {}".format(", ".join(data["unmapped_teams"])))
        print("  Map them with setup_shortcut.py, or the combined figure is")
        print("  missing part of the area.")
        print("")

    # The point of the whole exercise.
    measured = [
        r for r in rows
        if not r["unmapped"] and r["metrics"]["cycle_time"]["p50"] is not None
    ]
    if len(measured) < 2:
        return

    print("=== Why this is one query and not a sum of the rows ===")
    naive = sum(r["metrics"]["cycle_time"]["p50"] for r in measured) / len(measured)
    truth = combined["cycle_time"]["p50"]
    print("  averaging the per-team medians: {}".format(humanise(naive)))
    print("  median of the actual tickets:   {}".format(humanise(truth)))

    if truth and abs(naive - truth) / truth > 0.1:
        factor = naive / truth if truth else 0
        print("")
        print("  Those differ by {:.1f}x. The first number is the median of".format(
            max(factor, 1 / factor if factor else 0)))
        print("  nothing that exists — it weights a small team the same as a")
        print("  large one. The combined row above is the real one, taken from")
        print("  {} tickets in a single query.".format(
            combined["cycle_time"]["count"]))
    else:
        print("")
        print("  Close, this time — the teams happen to be similar in size and")
        print("  speed. It will not stay that way, which is why the combined")
        print("  figure is computed from the tickets rather than from the rows.")

    counts = sum(r["metrics"]["throughput"] for r in measured)
    if counts == combined["throughput"]:
        print("")
        print("  Throughput does add up ({} = {}), as counts should. It is the".format(
            counts, combined["throughput"]))
        print("  averages and ratios that cannot be combined that way.")


if __name__ == "__main__":
    main()
