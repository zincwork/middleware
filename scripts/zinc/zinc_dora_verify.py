#!/usr/bin/env python3
"""
zinc_dora_verify.py — does Option A actually give you different numbers per team?

Pulls the DORA metrics for every Middleware team from your local instance, over
the same date window, and prints them side by side. Then says plainly whether
any teams came out identical — which is what happens when teams share the same
repositories, and is the thing that decides whether Option A is good enough.

Usage:
    python3 zinc_dora_verify.py
    python3 zinc_dora_verify.py --days 90 --branch-mode PROD

Standard library only. No pip install required.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DEFAULT_BASE_URL = "http://localhost:3333"
# Middleware's interval validator rejects windows longer than this.
MAX_DAYS = 105


def request(base_url, path, params=None):
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-dora-verify")

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:600]
        return {"_error": "HTTP {}: {}".format(err.code, detail)}
    except urllib.error.URLError as err:
        sys.exit(
            "Could not reach Middleware at {}: {}\n"
            "Is it running? Check with: docker compose ps".format(base_url, err.reason)
        )


def dig(obj, *keys, default=None):
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
    return default if obj is None else obj


def hours(seconds):
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    return seconds / 3600.0


def fmt(value, suffix="", places=1):
    if value is None:
        return "-"
    if isinstance(value, float):
        return "{:.{p}f}{}".format(value, suffix, p=places)
    return "{}{}".format(value, suffix)


def extract(payload):
    """Pull the headline figures out of a dora_metrics response."""
    return {
        "prs": dig(payload, "lead_time_stats", "current", "pr_count"),
        "lead_time_h": hours(dig(payload, "lead_time_stats", "current", "lead_time")),
        "deployments": dig(
            payload, "deployment_frequency_stats", "current", "total_deployments"),
        "deploys_per_day": dig(
            payload, "deployment_frequency_stats", "current",
            "avg_daily_deployment_frequency"),
        "cfr": dig(payload, "change_failure_rate_stats", "current",
                   "change_failure_rate"),
        "incidents": dig(payload, "mean_time_to_restore_stats", "current",
                         "incident_count"),
        "mttr_h": hours(dig(payload, "mean_time_to_restore_stats", "current",
                            "mean_time_to_recovery")),
        "repos": len(dig(payload, "assigned_repos", default=[]) or []),
        "unsynced": len(dig(payload, "unsynced_repos", default=[]) or []),
    }


# The comparison key deliberately excludes repo count: two teams can hold
# different numbers of repos and still produce identical metrics if the extra
# repos are inactive.
COMPARE_KEYS = ("prs", "lead_time_h", "deployments", "cfr", "incidents", "mttr_h")


def main():
    ap = argparse.ArgumentParser(
        description="Compare DORA metrics across all Middleware teams."
    )
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--days", type=int, default=30,
                    help="Window size in days, max {} (default: %(default)s)"
                         .format(MAX_DAYS))
    ap.add_argument("--branch-mode", default="PROD",
                    choices=["PROD", "ALL", "CUSTOM"],
                    help="PROD uses each team's production branches (default). "
                         "ALL ignores branch filtering entirely.")
    ap.add_argument("--json", metavar="PATH",
                    help="Also write the raw figures to a JSON file.")
    args = ap.parse_args()

    if args.days > MAX_DAYS:
        sys.exit("Middleware caps the query interval at {} days.".format(MAX_DAYS))

    session = request(args.base_url, "/api/auth/session")
    org_id = dig(session, "org", "id")
    if not org_id:
        sys.exit("Could not read an org id from /api/auth/session.")

    listing = request(args.base_url, "/api/resources/orgs/{}/teams/v2".format(org_id))
    teams = sorted(listing.get("teams") or [], key=lambda t: t["name"])
    if not teams:
        sys.exit("No teams in Middleware yet. Run zinc_dora_apply.py first.")

    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=args.days)
    window = {
        "org_id": org_id,
        "from_date": from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "to_date": to_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "branch_mode": args.branch_mode,
    }

    print("Window: {} to {}  ({} days, branch mode {})".format(
        from_date.date(), to_date.date(), args.days, args.branch_mode))
    print("")

    rows = []
    for team in teams:
        print("  fetching {} ...".format(team["name"]), file=sys.stderr)
        payload = request(
            args.base_url,
            "/api/internal/team/{}/dora_metrics".format(team["id"]),
            window,
        )
        if "_error" in payload:
            rows.append({"team": team["name"], "error": payload["_error"]})
            continue
        row = extract(payload)
        row["team"] = team["name"]
        rows.append(row)

    print("")
    header = ("| Team | Repos | PRs | Lead time (h) | Deploys | /day | CFR % "
              "| Incidents | MTTR (h) |")
    print(header)
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        if "error" in r:
            print("| {} | error: {} |".format(r["team"], r["error"][:60]))
            continue
        print("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["team"],
            fmt(r["repos"]),
            fmt(r["prs"]),
            fmt(r["lead_time_h"]),
            fmt(r["deployments"]),
            fmt(r["deploys_per_day"], places=2),
            fmt(r["cfr"]),
            fmt(r["incidents"]),
            fmt(r["mttr_h"]),
        ))
    print("")

    # --- the actual verdict -------------------------------------------------
    good = [r for r in rows if "error" not in r]
    if len(good) < 2:
        print("Not enough teams returned data to compare.")
        return

    signatures = {}
    for r in good:
        sig = tuple(r[k] for k in COMPARE_KEYS)
        signatures.setdefault(sig, []).append(r["team"])

    duplicates = [names for names in signatures.values() if len(names) > 1]
    empty = [r["team"] for r in good if not r["prs"] and not r["deployments"]]
    unsynced = [r["team"] for r in good if r["unsynced"]]

    if duplicates:
        print("IDENTICAL FIGURES — Option A is not separating these teams:")
        for names in duplicates:
            print("  - {}".format(", ".join(names)))
        print("")
        print("  This means they resolve to the same repositories. Repository-scoped")
        print("  teams cannot tell them apart. If per-team numbers are the point,")
        print("  this is your evidence for Option B (filter by team members).")
    else:
        print("All teams produced distinct figures. Option A is working.")

    if empty:
        print("")
        print("EMPTY: {}".format(", ".join(empty)))
        print("  No PRs and no deployments in this window. Either genuinely quiet,")
        print("  or the production branches are wrong — check the Branch selector")
        print("  shows the right branch, and try --branch-mode ALL to compare.")

    if unsynced:
        print("")
        print("UNSYNCED REPOS: {}".format(", ".join(unsynced)))
        print("  These teams have repositories Middleware has not synced yet, so")
        print("  their figures are incomplete. Wait for the sync and re-run.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"window": window, "rows": rows}, fh, indent=2)
            fh.write("\n")
        print("")
        print("Raw figures written to {}".format(args.json))


if __name__ == "__main__":
    main()
