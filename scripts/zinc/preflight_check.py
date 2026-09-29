#!/usr/bin/env python3
"""
preflight_check.py — prove the ticket layer changes nothing that already works.

Run it BEFORE installing to capture a snapshot of every existing DORA figure,
and AFTER to prove they are byte-identical. That is the actual test of
"non-destructive": not that the code looks additive, but that the numbers did
not move.

    python3 preflight_check.py --snapshot before.json     # before installing
    ...install, migrate, sync...
    python3 preflight_check.py --compare before.json      # after

It also checks the things that could make the install unsafe: a dirty working
tree, a migration timestamp that would sort before an applied one, and whether
the ticket tables already exist.

Read-only. Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DEFAULT_REPO = os.path.expanduser("~/Downloads/middleware")
DEFAULT_BASE_URL = "http://localhost:3333"
MIGRATION = "20260918120000_add_ticket_tables.sql"
SNAPSHOT_DAYS = 30

OK = "  ok    "
WARN = "  WARN  "
FAIL = "  FAIL  "


def http(url, timeout=180):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-preflight")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        return {"_error": "HTTP {}".format(err.code)}
    except urllib.error.URLError as err:
        return {"_error": "unreachable: {}".format(err.reason)}


def dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def git(repo, *args):
    try:
        return subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:
        return ""


# --------------------------------------------------------------------------
# Static checks
# --------------------------------------------------------------------------

def run_checks(repo, base_url):
    problems = warnings = 0
    print("Repo: {}".format(repo))
    print("")
    print("Static checks")

    if not os.path.isdir(os.path.join(repo, "web-server")):
        print(FAIL + "not a Middleware checkout (no web-server/)")
        return 1, 0
    print(OK + "Middleware checkout found")

    remote = git(repo, "remote", "get-url", "origin")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    print(OK + "origin: {}".format(remote or "(none)"))
    print(OK + "branch: {}".format(branch or "(unknown)"))

    dirty = git(repo, "status", "--porcelain")
    if dirty:
        count = len(dirty.splitlines())
        print(WARN + "{} uncommitted change(s). Commit or stash first so the "
                     "install is distinguishable from your own edits.".format(count))
        warnings += 1
    else:
        print(OK + "working tree clean")

    if branch in ("main", "master"):
        print(WARN + "you are on '{}'. Create a branch first:".format(branch))
        print("            git -C {} checkout -b zinc/cockpit-tickets".format(repo))
        warnings += 1

    # Migration ordering. dbmate applies pending migrations in filename order,
    # so a new file must sort AFTER everything already applied.
    mig_dir = os.path.join(repo, "database-docker", "db", "migrations")
    all_migrations = sorted(f for f in os.listdir(mig_dir) if f.endswith(".sql")) \
        if os.path.isdir(mig_dir) else []
    # Compare against everything EXCEPT our own migration: once installed it is
    # the newest file, and comparing it against itself would always fail.
    existing = [f for f in all_migrations if f != MIGRATION]

    if not all_migrations:
        print(FAIL + "no migrations directory found at {}".format(mig_dir))
        problems += 1
    else:
        latest = existing[-1] if existing else None
        if latest and MIGRATION <= latest:
            print(FAIL + "migration {} would sort before the applied {}. "
                         "dbmate would never run it.".format(MIGRATION, latest))
            problems += 1
        else:
            print(OK + "migration sorts after {}".format(latest or "(none)"))

        if MIGRATION in all_migrations:
            print(WARN + "migration already present — install has been run before")
            warnings += 1

    # Confirm init_db.sh applies rather than resets. This is the single most
    # important safety property: `dbmate up` never drops data.
    init_sh = os.path.join(repo, "setup_utils", "init_db.sh")
    if os.path.isfile(init_sh):
        with open(init_sh, encoding="utf-8") as fh:
            body = fh.read()
        if "dbmate" in body and " up" in body and "drop" not in body.lower():
            print(OK + "init_db.sh runs 'dbmate up' and does not drop the database")
        else:
            print(WARN + "could not confirm init_db.sh is non-destructive — read it "
                         "before proceeding")
            warnings += 1

    # Options B and C, informational: Layer 1 does not depend on them, but the
    # Cockpit view later will.
    filter_py = os.path.join(
        repo, "backend/analytics_server/mhq/store/models/code/filter.py"
    )
    if os.path.isfile(filter_py):
        with open(filter_py, encoding="utf-8") as fh:
            body = fh.read()
        print(OK + "Option B author filter: {}".format(
            "present" if "_authors_query" in body else "absent"))
        print(OK + "Option C branch filter: {}".format(
            "present" if "head_branches" in body else "absent"))

    print("")
    print("Live checks")
    session = http(base_url.rstrip("/") + "/api/auth/session")
    if "_error" in session:
        print(WARN + "Middleware not reachable at {} ({}). Snapshot and "
                     "comparison need it running.".format(base_url, session["_error"]))
        warnings += 1
        return problems, warnings

    org_id = dig(session, "org", "id")
    print(OK + "Middleware reachable, org {}".format(org_id))

    return problems, warnings


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------

METRIC_PATHS = {
    "prs": ("lead_time_stats", "current", "pr_count"),
    "lead_time": ("lead_time_stats", "current", "lead_time"),
    "deployments": ("deployment_frequency_stats", "current", "total_deployments"),
    "cfr": ("change_failure_rate_stats", "current", "change_failure_rate"),
    "incidents": ("mean_time_to_restore_stats", "current", "incident_count"),
    "mttr": ("mean_time_to_restore_stats", "current", "mean_time_to_recovery"),
}


def take_snapshot(base_url, days):
    session = http(base_url.rstrip("/") + "/api/auth/session")
    org_id = dig(session, "org", "id")
    if not org_id:
        sys.exit("Could not read an org id from Middleware. Is it running?")

    listing = http(
        "{}/api/resources/orgs/{}/teams/v2".format(base_url.rstrip("/"), org_id)
    )
    teams = sorted(listing.get("teams") or [], key=lambda t: t["name"])
    if not teams:
        sys.exit("No Middleware teams found — nothing to snapshot.")

    # Fixed window, so a later comparison measures the same period rather than
    # a rolling one.
    until = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    since = until - timedelta(days=days)
    window = {
        "org_id": org_id,
        "from_date": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "to_date": until.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "branch_mode": "PROD",
    }

    rows = {}
    for team in teams:
        print("  reading {} ...".format(team["name"]), file=sys.stderr)
        payload = http(
            "{}/api/internal/team/{}/dora_metrics?{}".format(
                base_url.rstrip("/"), team["id"], urllib.parse.urlencode(window)
            )
        )
        if "_error" in payload:
            rows[team["name"]] = {"error": payload["_error"]}
            continue
        rows[team["name"]] = {
            name: dig(payload, *path) for name, path in METRIC_PATHS.items()
        }

    return {"window": window, "teams": rows}


def print_snapshot(snapshot):
    print("| Team | PRs | Lead time | Deploys | CFR % | Incidents | MTTR |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for team, row in snapshot["teams"].items():
        if "error" in row:
            print("| {} | error: {} |".format(team, row["error"]))
            continue
        print("| {} | {} | {} | {} | {} | {} | {} |".format(
            team,
            *[row.get(k) if row.get(k) is not None else "-" for k in METRIC_PATHS]
        ))


def compare(before, after):
    print("")
    print("Comparison — every figure must be identical")
    print("")

    before_teams = before.get("teams", {})
    after_teams = after.get("teams", {})

    if before.get("window") != after.get("window"):
        print(WARN + "the snapshot windows differ, so a difference may be time, "
                     "not the install. Re-snapshot with the same --days.")

    diffs = []
    for team in sorted(set(before_teams) | set(after_teams)):
        b = before_teams.get(team)
        a = after_teams.get(team)
        if b is None:
            diffs.append((team, "team is new since the snapshot", None, None))
            continue
        if a is None:
            diffs.append((team, "team has disappeared", None, None))
            continue
        for metric in METRIC_PATHS:
            if b.get(metric) != a.get(metric):
                diffs.append((team, metric, b.get(metric), a.get(metric)))

    if not diffs:
        print("  NOTHING CHANGED. {} team(s), {} metric(s) each, all identical."
              .format(len(after_teams), len(METRIC_PATHS)))
        print("  The ticket layer is additive as intended.")
        return 0

    print("  DIFFERENCES FOUND — investigate before going further:")
    for team, metric, b, a in diffs:
        if b is None and a is None:
            print("    {} — {}".format(team, metric))
        else:
            print("    {:<20} {:<14} before={!s:<12} after={!s}".format(
                team, metric, b, a))
    print("")
    print("  Nothing in Layer 1 touches DORA queries, so a difference here most")
    print("  likely means new PRs or deployments arrived between snapshots, or a")
    print("  team's repos changed. Re-run the snapshot on a closed historic")
    print("  window to rule that out.")
    return 1


def main():
    ap = argparse.ArgumentParser(
        description="Prove the ticket layer does not disturb existing metrics."
    )
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--days", type=int, default=SNAPSHOT_DAYS)
    ap.add_argument("--snapshot", metavar="PATH",
                    help="Write a before-snapshot of every team's DORA figures.")
    ap.add_argument("--compare", metavar="PATH",
                    help="Re-read the figures and diff against a snapshot.")
    ap.add_argument("--checks-only", action="store_true")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.expanduser(args.repo))
    problems, warnings = run_checks(repo, args.base_url)

    print("")
    print("{} problem(s), {} warning(s)".format(problems, warnings))

    if problems:
        print("")
        print("Fix the problems above before installing.")
        sys.exit(1)

    if args.checks_only:
        return

    if args.snapshot:
        print("")
        print("Snapshot ({} days)".format(args.days))
        snapshot = take_snapshot(args.base_url, args.days)
        with open(args.snapshot, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2)
        print("")
        print_snapshot(snapshot)
        print("")
        print("Written to {}. Keep it — you need it to prove nothing moved."
              .format(args.snapshot))
        return

    if args.compare:
        try:
            with open(args.compare, encoding="utf-8") as fh:
                before = json.load(fh)
        except (OSError, json.JSONDecodeError) as err:
            sys.exit("Could not read snapshot {}: {}".format(args.compare, err))
        print("")
        print("Re-reading ({} days)".format(args.days))
        after = take_snapshot(args.base_url, args.days)
        print("")
        print_snapshot(after)
        sys.exit(compare(before, after))

    print("")
    print("Checks passed. Next:")
    print("  python3 preflight_check.py --snapshot before.json")


if __name__ == "__main__":
    main()
