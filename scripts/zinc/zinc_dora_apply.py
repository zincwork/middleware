#!/usr/bin/env python3
"""
zinc_dora_apply.py — create Middleware teams from zinc_teams.json.

Talks to your locally running Middleware instance and, for each GitHub team in
the config, creates (or updates) a matching Middleware team with that team's
repositories assigned, then sets the production branches. Also creates an
"All Zinc" team holding every repository, which is what gives you the org-wide
view — Middleware has no org-level query path, so "all of Zinc" has to be a
team containing all repos.

Idempotent: a team whose name already exists in Middleware is updated in place
rather than duplicated.

Usage:
    python3 zinc_dora_apply.py --dry-run      # inspect the payloads first
    python3 zinc_dora_apply.py

Standard library only. No pip install required.

Endpoints used (all local, no auth in the self-hosted build):
    GET  /api/auth/session                              -> org id
    GET  /api/resources/orgs/{org_id}/teams/v2          -> existing teams
    POST /api/resources/orgs/{org_id}/teams/v2          -> create team + repos
    PATCH /api/resources/orgs/{org_id}/teams/v2         -> update team + repos
    GET  /api/internal/team/{team_id}/repo_branches     -> org_repo_id per repo
    PUT  /api/internal/team/{team_id}/repo_branches     -> set prod branches
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://localhost:3333"
ALL_TEAM_DEFAULT = "All Zinc"


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------

def request(base_url, method, path, body=None, tolerate_missing=False):
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-dora-apply")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        # An endpoint that does not exist in this build is not an error when
        # the caller is only probing for it (e.g. the CircleCI route on an
        # install without Option C).
        if tolerate_missing:
            return None
        detail = err.read().decode("utf-8", errors="replace")[:800]
        sys.exit(
            "{} {} failed with HTTP {}\n\n{}\n\n"
            "If this is a 4xx, the request shape is wrong — re-run with --dry-run\n"
            "and check the payload. If it is a 5xx, check the Middleware logs\n"
            "with: docker compose logs -f".format(method, path, err.code, detail)
        )
    except urllib.error.URLError as err:
        if tolerate_missing:
            return None
        sys.exit(
            "Could not reach Middleware at {}: {}\n\n"
            "Is it running? The web app listens on port 3333 by default.\n"
            "Check with: docker compose ps".format(base_url, err.reason)
        )


# --------------------------------------------------------------------------
# Payload building
# --------------------------------------------------------------------------

def org_repos_payload(org, repos):
    """Build the org_repos map the teams/v2 endpoint expects.

    Shape is {"<github org login>": [ {repo}, ... ]}. The route injects the map
    key as the repo's `org` field, which the backend reads as org_name.
    """
    return {
        org: [
            {
                # Must be the GitHub numeric repo id as a string — this is what
                # Middleware keys OrgRepo rows on, so it attaches to the rows
                # your existing sync already created.
                "idempotency_key": str(r["id"]),
                "name": r["name"],
                "slug": r["name"],
                "provider": "github",
                "default_branch": r["default_branch"],
                "deployment_type": r.get("deployment_type", "PR_MERGE"),
                "repo_workflows": r.get("repo_workflows", []),
            }
            for r in repos
        ]
    }


def prod_branch_payload(team_id, repo_branch_rows, wanted_by_name):
    """Build the team_repos_data list for the repo_branches PUT.

    org_repo_id is a Middleware UUID that only exists after the repos have been
    assigned, which is why this is a second pass rather than part of the create.
    """
    rows = []
    for row in repo_branch_rows:
        wanted = wanted_by_name.get(row["name"])
        if not wanted:
            continue
        rows.append({
            "team_id": team_id,
            "org_repo_id": row["org_repo_id"],
            "name": row["name"],
            "prod_branches": wanted,
            "is_active": True,
        })
    return rows


# --------------------------------------------------------------------------
# Work
# --------------------------------------------------------------------------

def capture_circleci_config(base_url, org_id):
    """Snapshot any active CircleCI deploy-job configuration.

    This script assigns repos to teams through the teams/v2 route, and that
    route DEACTIVATES every DEPLOYMENT workflow for a team's repos before
    re-inserting the ones in the payload (teams/v2.ts, "Step 2: Disable all
    workflows"). This config carries no workflows, so a CircleCI setup would
    be silently switched off — and every repo here is PR_MERGE, which also
    reverts deployment_type.

    So: capture it first, put it back afterwards.
    """
    result = request(
        base_url, "GET", "/api/resources/orgs/{}/circleci_workflows".format(org_id),
        tolerate_missing=True,
    )
    if result is None:
        return []

    restore = []
    for row in result.get("workflows") or []:
        if not row.get("is_active"):
            continue
        meta = row.get("meta") or {}
        workflow_name = meta.get("workflow_name")
        job_name = meta.get("job_name")
        if not (workflow_name and job_name):
            # Fall back to splitting "<workflow>/<job>".
            parts = (row.get("provider_workflow_id") or "").split("/", 1)
            if len(parts) != 2:
                continue
            workflow_name, job_name = parts
        restore.append({
            "repo_name": row.get("repo_name"),
            "workflow_name": workflow_name,
            "job_name": job_name,
            "branch": meta.get("branch") or "main",
            "project_slug": meta.get("project_slug") or "",
        })
    return restore


def restore_circleci_config(base_url, org_id, restore, dry_run):
    if not restore:
        return
    print("Restoring CircleCI deploy-job configuration ({} repo(s))".format(
        len(restore)))
    for entry in restore:
        print("  {} -> {}/{} on {}".format(
            entry["repo_name"], entry["workflow_name"], entry["job_name"],
            entry["branch"]))
    if dry_run:
        print("  (dry run — not sent)")
        return
    request(
        base_url, "PUT",
        "/api/resources/orgs/{}/circleci_workflows".format(org_id),
        {"workflows": restore},
    )
    print("  restored — deployment_type is back to WORKFLOW")


def apply_team(base_url, org_id, org, name, repos, existing_by_name, dry_run):
    """Create or update one Middleware team. Returns the team id, or None on dry run."""
    payload = {"name": name, "org_repos": org_repos_payload(org, repos)}
    existing = existing_by_name.get(name.strip().lower())

    if existing:
        payload["id"] = existing["id"]
        method, verb = "PATCH", "update"
    else:
        method, verb = "POST", "create"

    print("  {} team '{}' with {} repo(s)".format(verb, name, len(repos)))

    if dry_run:
        print("    {} /api/resources/orgs/{}/teams/v2".format(method, org_id))
        print("    " + json.dumps(payload, indent=2).replace("\n", "\n    "))
        return None

    result = request(
        base_url, method, "/api/resources/orgs/{}/teams/v2".format(org_id), payload
    )
    team = result.get("team") or {}
    team_id = team.get("id") or (existing or {}).get("id")
    if not team_id:
        sys.exit(
            "Middleware did not return a team id for '{}'.\nResponse: {}"
            .format(name, json.dumps(result)[:500])
        )
    return team_id


def apply_prod_branches(base_url, team_id, team_name, repos, dry_run):
    wanted_by_name = {
        r["name"]: r.get("prod_branches") or ["^" + r["default_branch"] + "$"]
        for r in repos
    }

    if dry_run:
        print("    would set production branches: {}".format(
            json.dumps(wanted_by_name)))
        return

    current = request(
        base_url, "GET", "/api/internal/team/{}/repo_branches".format(team_id)
    )
    if not isinstance(current, list):
        print("    warning: unexpected repo_branches response, skipping prod branches")
        return

    rows = prod_branch_payload(team_id, current, wanted_by_name)
    if not rows:
        print("    warning: no repos matched by name, prod branches left untouched")
        return

    request(
        base_url,
        "PUT",
        "/api/internal/team/{}/repo_branches".format(team_id),
        {"team_repos_data": rows},
    )
    print("    set production branches on {} repo(s)".format(len(rows)))

    missing = set(wanted_by_name) - {r["name"] for r in rows}
    if missing:
        print("    note: not found in Middleware yet: {}".format(
            ", ".join(sorted(missing))))
        print("          these repos have not been synced. Run a sync, then re-run.")


def main():
    ap = argparse.ArgumentParser(
        description="Create Middleware teams from a zinc_dora_discover.py config."
    )
    ap.add_argument("--config", default="zinc_teams.json")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL,
                    help="Middleware web app base URL (default: %(default)s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the exact payloads without sending anything.")
    ap.add_argument("--all-team-name", default=ALL_TEAM_DEFAULT,
                    help="Name for the org-wide team (default: %(default)s)")
    ap.add_argument("--skip-all-team", action="store_true",
                    help="Do not create the org-wide team.")
    ap.add_argument("--only", action="append", metavar="SLUG",
                    help="Only apply these GitHub team slugs. Repeatable.")
    ap.add_argument("--skip-prod-branches", action="store_true",
                    help="Do not set production branches.")
    ap.add_argument("--skip-circleci-restore", action="store_true",
                    help="Do not put the CircleCI deploy-job configuration back "
                         "afterwards. Only use this if you want deployment "
                         "frequency to go back to counting merges into main.")
    args = ap.parse_args()

    try:
        with open(args.config, encoding="utf-8") as fh:
            config = json.load(fh)
    except FileNotFoundError:
        sys.exit("Config not found: {}\nRun zinc_dora_discover.py first."
                 .format(args.config))
    except json.JSONDecodeError as err:
        sys.exit("Config is not valid JSON ({}): {}".format(args.config, err))

    org = config["org"]

    session = request(args.base_url, "GET", "/api/auth/session")
    org_id = (session.get("org") or {}).get("id")
    if not org_id:
        sys.exit(
            "Could not read an org id from /api/auth/session.\n"
            "Response: {}\n\n"
            "Middleware may not have finished first-run setup. Open {} in a\n"
            "browser and complete onboarding, then re-run."
            .format(json.dumps(session)[:400], args.base_url)
        )
    print("Middleware org id: {}".format(org_id))

    listing = request(
        args.base_url, "GET", "/api/resources/orgs/{}/teams/v2".format(org_id)
    )
    existing_by_name = {
        t["name"].strip().lower(): t for t in (listing.get("teams") or [])
    }
    print("Existing Middleware teams: {}".format(
        ", ".join(sorted(t["name"] for t in (listing.get("teams") or []))) or "none"))
    print("")

    jobs = []
    for team in config["teams"]:
        if args.only and team["slug"] not in args.only:
            continue
        if not team["repos"]:
            print("Skipping '{}' — no repositories in the config.".format(team["slug"]))
            continue
        jobs.append((team["middleware_team_name"], team["repos"]))

    if not args.skip_all_team:
        jobs.append((args.all_team_name, config["all_repos"]))

    if not jobs:
        sys.exit("Nothing to do. Check --only, or the config's team repo lists.")

    # Assigning repos to teams deactivates every DEPLOYMENT workflow on those
    # repos and resets deployment_type to whatever this config says (PR_MERGE).
    # Capture any CircleCI setup now so it can be put back afterwards.
    circleci_restore = capture_circleci_config(args.base_url, org_id)
    if circleci_restore:
        print("CircleCI integration detected on {} repo(s).".format(
            len(circleci_restore)))
        print("This run would switch it off, so it will be restored at the end.")
        print("")
    if args.skip_circleci_restore:
        circleci_restore = []

    if args.dry_run:
        print("DRY RUN — nothing will be sent.\n")

    for name, repos in jobs:
        team_id = apply_team(
            args.base_url, org_id, org, name, repos, existing_by_name, args.dry_run
        )
        if not args.skip_prod_branches:
            if args.dry_run:
                apply_prod_branches(args.base_url, None, name, repos, True)
            else:
                apply_prod_branches(args.base_url, team_id, name, repos, False)
        print("")

    restore_circleci_config(
        args.base_url, org_id, circleci_restore, args.dry_run
    )
    if circleci_restore and not args.dry_run:
        print("")

    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to apply.")
    else:
        print("Done. Middleware kicks off a repo sync after each team change, so")
        print("give it a few minutes before reading the metrics, then run:")
        print("    python3 zinc_dora_verify.py")


if __name__ == "__main__":
    main()
