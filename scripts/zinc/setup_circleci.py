#!/usr/bin/env python3
"""
setup_circleci.py — point Middleware at your CircleCI deploy jobs.

Three things:
  1. validates your CircleCI token against the CircleCI API
  2. stores it in Middleware's Integration table under the name `circle_ci`
  3. configures each repo's deploy job and switches the repo from PR_MERGE to
     WORKFLOW, so a merge into main stops being counted as a deployment

The token is read from the CIRCLECI_TOKEN environment variable. It is sent only
to the CircleCI API (to validate it) and to your local Middleware (to store it,
encrypted with the same key as your GitHub token). It is never written to a
file or printed.

Usage:
    export CIRCLECI_TOKEN=...
    python3 setup_circleci.py --dry-run
    python3 setup_circleci.py

    python3 setup_circleci.py --revert     # back to PR_MERGE

Standard library only.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://localhost:3333"
CIRCLE_CI_API = "https://circleci.com/api/v2"

# Read off Zinc's .circleci/config.yml. Each entry is:
#   repo name in Middleware -> workflow, deploy job, production branch
#
# mvp-app's job is called deploy-master but filters on `main` and deploys
# zinc-app-production-cluster. The name is a leftover.
DEFAULT_REPOS = [
    {
        "repo_name": "mvp-api",
        "workflow_name": "build-and-deploy",
        "job_name": "deploy-production",
        "branch": "main",
        "project_slug": "gh/zincwork/mvp-api",
    },
    {
        "repo_name": "mvp-app",
        "workflow_name": "build-and-deploy",
        "job_name": "deploy-master",
        "branch": "main",
        "project_slug": "gh/zincwork/mvp-app",
    },
]


def _token():
    token = os.environ.get("CIRCLECI_TOKEN", "").strip()
    if not token:
        sys.exit(
            "CIRCLECI_TOKEN is not set.\n\n"
            "Create one at https://app.circleci.com/settings/user/tokens\n"
            "(or reuse the existing one), then:\n"
            "    export CIRCLECI_TOKEN=<your token>\n\n"
            "It is only sent to CircleCI and to your local Middleware."
        )
    return token


def http(url, method="GET", body=None, headers=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-setup-circleci")
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError("{} {} -> HTTP {}\n{}".format(method, url, err.code, detail))
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach {}: {}".format(url, err.reason))


# --------------------------------------------------------------------------

def check_circleci_token(token, repos):
    """Confirm the token works and can see each project."""
    headers = {"Circle-Token": token}

    try:
        me = http(CIRCLE_CI_API + "/me", headers=headers)
    except RuntimeError as err:
        sys.exit(
            "CircleCI rejected the token.\n\n{}\n\n"
            "Check it is current and has not been revoked.".format(err)
        )

    print("CircleCI token valid (user: {})".format(me.get("login") or me.get("name")))

    problems = []
    for repo in repos:
        slug = repo["project_slug"]
        url = "{}/project/{}/pipeline?branch={}".format(
            CIRCLE_CI_API, slug, repo["branch"]
        )
        try:
            payload = http(url, headers=headers)
        except RuntimeError as err:
            problems.append((slug, str(err).splitlines()[0]))
            continue

        count = len(payload.get("items") or [])
        print("  {:<28} {} recent pipeline(s) on {}".format(slug, count, repo["branch"]))
        if not count:
            problems.append((slug, "no pipelines on branch '{}'".format(repo["branch"])))

    if problems:
        print("")
        print("PROBLEMS with CircleCI project access:")
        for slug, message in problems:
            print("  {} — {}".format(slug, message))
        print("")
        print("Either the project slug is wrong, the branch name is wrong, or the")
        print("token's owner cannot see the project. Check the slug against a")
        print("CircleCI URL: app.circleci.com/pipelines/<slug>/...")
        sys.exit(1)


def get_org_id(base_url):
    session = http(base_url.rstrip("/") + "/api/auth/session")
    org_id = (session.get("org") or {}).get("id")
    if not org_id:
        sys.exit(
            "Could not read an org id from /api/auth/session.\n"
            "Is Middleware running? Check with: docker compose ps"
        )
    return org_id


def store_token(base_url, org_id, token, dry_run):
    url = "{}/api/resources/orgs/{}/integration".format(base_url.rstrip("/"), org_id)
    body = {
        "provider": "circle_ci",
        "the_good_stuff": token,
        "meta_data": {"source": "setup_circleci.py"},
    }

    if dry_run:
        print("  POST {}".format(url))
        print("       provider=circle_ci, the_good_stuff=<redacted>")
        return

    http(url, method="POST", body=body)
    print("  stored CircleCI token as integration 'circle_ci'")


def configure_workflows(base_url, org_id, repos, dry_run):
    url = "{}/api/resources/orgs/{}/circleci_workflows".format(
        base_url.rstrip("/"), org_id
    )
    body = {"workflows": repos}

    if dry_run:
        print("  PUT {}".format(url))
        print("      " + json.dumps(body, indent=2).replace("\n", "\n      "))
        return

    try:
        result = http(url, method="PUT", body=body)
    except RuntimeError as err:
        sys.exit(
            "{}\n\n"
            "If this says a repo is not active in Middleware, assign it to a team\n"
            "and let a sync finish first — the RepoWorkflow row needs an OrgRepo\n"
            "to hang off.".format(err)
        )

    for entry in result.get("configured", []):
        print("  configured {}".format(entry["provider_workflow_id"]))
    print("  switched those repos from PR_MERGE to WORKFLOW")


def revert(base_url, org_id, repos, dry_run):
    url = "{}/api/resources/orgs/{}/circleci_workflows".format(
        base_url.rstrip("/"), org_id
    )
    body = {"repo_names": [r["repo_name"] for r in repos]}

    if dry_run:
        print("  DELETE {} with {}".format(url, json.dumps(body)))
        return

    http(url, method="DELETE", body=body)
    print("  deactivated the CircleCI workflows and set deployment_type back")
    print("  to PR_MERGE for: {}".format(", ".join(body["repo_names"])))
    print("")
    print("The stored token is left in place. Remove it from the integrations")
    print("page if you want it gone. Already-synced RepoWorkflowRuns rows are")
    print("harmless — they are ignored once the workflow is inactive.")


def main():
    ap = argparse.ArgumentParser(
        description="Point Middleware at your CircleCI deploy jobs."
    )
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--config", help="JSON file overriding the repo list.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true",
                    help="Switch back to PR_MERGE and deactivate the CircleCI "
                         "workflows.")
    ap.add_argument("--skip-token-check", action="store_true",
                    help="Do not call the CircleCI API first. Faster, but you "
                         "lose the check that each project is visible.")
    args = ap.parse_args()

    repos = DEFAULT_REPOS
    if args.config:
        try:
            with open(args.config, encoding="utf-8") as fh:
                repos = json.load(fh)["repos"]
        except (OSError, KeyError, json.JSONDecodeError) as err:
            sys.exit("Could not read repo list from {}: {}".format(args.config, err))

    print("Middleware: {}".format(args.base_url))
    org_id = get_org_id(args.base_url)
    print("Org: {}".format(org_id))
    print("")

    if args.dry_run:
        print("DRY RUN — nothing will be written.\n")

    if args.revert:
        revert(args.base_url, org_id, repos, args.dry_run)
        return

    print("Repos to configure:")
    for repo in repos:
        print("  {:<10} {}/{} on {}".format(
            repo["repo_name"], repo["workflow_name"], repo["job_name"], repo["branch"]))
    print("")

    if not args.skip_token_check:
        check_circleci_token(_token(), repos)
        print("")

    store_token(args.base_url, org_id, _token(), args.dry_run)
    configure_workflows(args.base_url, org_id, repos, args.dry_run)

    if args.dry_run:
        print("")
        print("Dry run complete. Re-run without --dry-run to apply.")
        return

    print("")
    print("Done. Now trigger a sync and wait for it to finish:")
    print("    curl -X POST http://localhost:9697/sync")
    print("")
    print("Then check the numbers:")
    print("    python3 verify_circleci.py")
    print("")
    print("Expect deployment frequency to FALL. It was counting merges into")
    print("main; it now counts approved production deploys.")


if __name__ == "__main__":
    main()
