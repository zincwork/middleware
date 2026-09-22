#!/usr/bin/env python3
"""
verify_circleci.py — does Middleware's deployment count match CircleCI's?

Counts successful runs of your deploy jobs straight from the CircleCI API, then
asks Middleware for the same window, and puts the two side by side. This is the
only way to know the integration is right rather than merely running.

Read-only. Touches nothing.

Usage:
    export CIRCLECI_TOKEN=...
    python3 verify_circleci.py
    python3 verify_circleci.py --days 90

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
CIRCLE_CI_API = "https://circleci.com/api/v2"
MAX_DAYS = 105
MAX_PAGES = 50

from_default_repos = [
    ("mvp-api", "gh/zincwork/mvp-api", "build-and-deploy", "deploy-production", "main"),
    ("mvp-app", "gh/zincwork/mvp-app", "build-and-deploy", "deploy-master", "main"),
]

NON_RUN = {"not_run", "blocked", "unauthorized", "not_running"}


def http(url, headers=None, timeout=60):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-verify-circleci")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError("HTTP {} for {}\n{}".format(err.code, url, detail))
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach {}: {}".format(url, err.reason))


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# CircleCI side — the ground truth
# --------------------------------------------------------------------------

def count_circleci_deploys(token, slug, workflow_name, job_name, branch, since):
    headers = {"Circle-Token": token}
    counts = {"success": 0, "failed": 0, "other": 0, "never_ran": 0}
    examples = []

    page_token = None
    for _ in range(MAX_PAGES):
        params = {"branch": branch}
        if page_token:
            params["page-token"] = page_token
        payload = http(
            "{}/project/{}/pipeline?{}".format(
                CIRCLE_CI_API, slug, urllib.parse.urlencode(params)
            ),
            headers=headers,
        )

        pipelines = payload.get("items") or []
        if not pipelines:
            break

        stop = False
        for pipeline in pipelines:
            created = parse_dt(pipeline.get("created_at"))
            if created and created < since:
                stop = True
                continue

            for workflow in (
                http(
                    "{}/pipeline/{}/workflow".format(CIRCLE_CI_API, pipeline["id"]),
                    headers=headers,
                ).get("items")
                or []
            ):
                if workflow.get("name") != workflow_name:
                    continue
                for job in (
                    http(
                        "{}/workflow/{}/job".format(CIRCLE_CI_API, workflow["id"]),
                        headers=headers,
                    ).get("items")
                    or []
                ):
                    if job.get("name") != job_name:
                        continue
                    status = job.get("status")
                    if status in NON_RUN or not job.get("started_at"):
                        counts["never_ran"] += 1
                    elif status == "success":
                        counts["success"] += 1
                        if len(examples) < 3:
                            examples.append(
                                "#{} {}".format(
                                    pipeline.get("number"), job.get("started_at")
                                )
                            )
                    elif status in ("failed", "infrastructure_fail", "timedout"):
                        counts["failed"] += 1
                    else:
                        counts["other"] += 1

        if stop:
            break
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    return counts, examples


# --------------------------------------------------------------------------
# Middleware side
# --------------------------------------------------------------------------

def middleware_deployments(base_url, org_id, team_id, since, until):
    params = {
        "org_id": org_id,
        "from_date": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "to_date": until.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "branch_mode": "PROD",
    }
    payload = http(
        "{}/api/internal/team/{}/dora_metrics?{}".format(
            base_url.rstrip("/"), team_id, urllib.parse.urlencode(params)
        )
    )
    stats = (payload.get("deployment_frequency_stats") or {}).get("current") or {}
    return {
        "total_deployments": stats.get("total_deployments"),
        "per_day": stats.get("avg_daily_deployment_frequency"),
        "repos": len(payload.get("assigned_repos") or []),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Compare Middleware's deployment count against CircleCI's."
    )
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--team", help="Only this Middleware team name (default: all).")
    args = ap.parse_args()

    if args.days > MAX_DAYS:
        sys.exit("Middleware caps the query interval at {} days.".format(MAX_DAYS))

    token = os.environ.get("CIRCLECI_TOKEN", "").strip()
    if not token:
        sys.exit("CIRCLECI_TOKEN is not set. export CIRCLECI_TOKEN=... first.")

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days)

    print("Window: {} to {} ({} days)".format(since.date(), until.date(), args.days))
    print("")

    print("=== CircleCI (ground truth) ===")
    circle_total = 0
    for name, slug, workflow, job, branch in from_default_repos:
        print("  {} — {}/{} on {}".format(name, workflow, job, branch), file=sys.stderr)
        try:
            counts, examples = count_circleci_deploys(
                token, slug, workflow, job, branch, since
            )
        except RuntimeError as err:
            print("  {:<10} ERROR: {}".format(name, str(err).splitlines()[0]))
            continue
        circle_total += counts["success"]
        print(
            "  {:<10} {} successful, {} failed, {} other, {} never ran (unapproved)"
            .format(name, counts["success"], counts["failed"], counts["other"],
                    counts["never_ran"])
        )
        for example in examples:
            print("             e.g. {}".format(example))
    print("")
    print("  CircleCI successful production deploys: {}".format(circle_total))
    print("")

    print("=== Middleware ===")
    session = http(args.base_url.rstrip("/") + "/api/auth/session")
    org_id = (session.get("org") or {}).get("id")
    if not org_id:
        sys.exit("Could not read an org id from Middleware.")

    listing = http(
        "{}/api/resources/orgs/{}/teams/v2".format(args.base_url.rstrip("/"), org_id)
    )
    teams = sorted(listing.get("teams") or [], key=lambda t: t["name"])
    if args.team:
        teams = [t for t in teams if t["name"] == args.team]
    if not teams:
        sys.exit("No matching Middleware teams.")

    print("| Team | Repos | Deployments | Per day |")
    print("|---|---:|---:|---:|")
    org_wide = None
    for team in teams:
        stats = middleware_deployments(
            args.base_url, org_id, team["id"], since, until
        )
        print("| {} | {} | {} | {} |".format(
            team["name"], stats["repos"],
            stats["total_deployments"] if stats["total_deployments"] is not None else "-",
            round(stats["per_day"], 2) if stats["per_day"] else "-",
        ))
        if stats["repos"] and (org_wide is None or stats["repos"] > org_wide[0]):
            org_wide = (stats["repos"], team["name"], stats["total_deployments"])
    print("")

    if org_wide is None or org_wide[2] is None:
        print("Middleware reported no deployments. Either the sync has not run")
        print("since setup, or the deploy job name does not match. Check:")
        print("    docker compose logs -f | grep -i circle")
        return

    widest_team, middleware_total = org_wide[1], org_wide[2]
    print("Comparing CircleCI ({}) against Middleware's widest team, '{}' ({}):"
          .format(circle_total, widest_team, middleware_total))

    if circle_total == middleware_total:
        print("  MATCH. The integration is correct.")
    else:
        diff = middleware_total - circle_total
        print("  MISMATCH of {:+d}.".format(diff))
        print("")
        if diff > 0:
            print("  Middleware is counting MORE than CircleCI. Likely causes:")
            print("   - a repo is still on PR_MERGE, so merges are still counted")
            print("   - a GitHub Actions workflow is also active on the same repo")
            print("   - the rollback workflow is being picked up")
        else:
            print("  Middleware is counting FEWER than CircleCI. Likely causes:")
            print("   - the sync has not caught up; run it again and re-check")
            print("   - the production branch in Middleware is not 'main', so the")
            print("     head_branch filter excludes these runs")
            print("   - the team does not have both repos assigned")


if __name__ == "__main__":
    main()
