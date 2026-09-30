#!/usr/bin/env python3
"""
fix_merge_to_deploy.py — correct the PRs audit_merge_to_deploy.py flagged.

Why this exists
----------------
merge_to_deploy ("Release" in the UI) is cached permanently on each PR. The
CircleCI timestamp-parsing bug (fixed in mhq/exapi/circle_ci.py,
2026-09-29) silently dropped some real deployments before they were
recorded, so PRs merged around those gaps got matched to whatever
deployment the matcher found next — in one observed case
(zincwork/mvp-api#6679), 4 weeks 5 days later than the deploy that actually
shipped it. audit_merge_to_deploy.py finds these; this writes the
correction.

What this actually does
------------------------
For every PR the audit's replay disagrees with:

  mismatch                          Writes the replay's own recomputed
                                     value directly — the SAME matching
                                     algorithm the real cache handler uses,
                                     already computed once by the audit, not
                                     a second calculation that could
                                     diverge. Persisted through
                                     CodeRepoService.update_prs, the exact
                                     method the real handler itself calls.

  cached_value_has_no_replay_match  Set to NULL. No correct value exists to
                                     write yet — the real deploy this PR
                                     needs is still missing from the data.
                                     NULL is the honest "not yet known"
                                     state, and is also what makes the real
                                     cache handler pick this PR up again on
                                     its own once that deploy is backfilled.

Defaults to a dry run. Pass --apply to actually write.

    python3 fix_merge_to_deploy.py                  # dry run (default)
    python3 fix_merge_to_deploy.py --apply
    python3 fix_merge_to_deploy.py --apply --repo-ids <uuid>,<uuid>

Standard library only.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

DEFAULT_BASE_URL = "http://localhost:3333"
ANALYTICS_URL = "http://localhost:9696"


def http(url, method="GET", timeout=120):
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-fix-merge-to-deploy")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError("HTTP {} {} {}\n{}".format(err.code, method, url, detail))
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach {}: {}".format(url, err.reason))


def humanise(seconds):
    if seconds is None:
        return "NULL"
    return str(timedelta(seconds=seconds))


def main():
    parser = argparse.ArgumentParser(
        description="Correct PRs audit_merge_to_deploy.py flagged. Dry run "
        "by default."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--analytics-url", default=ANALYTICS_URL)
    parser.add_argument(
        "--repo-ids",
        help="Comma-separated OrgRepo UUIDs to limit the fix to. Default: "
        "every active repo in the org.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write. Without this, only shows what would change.",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    analytics = args.analytics_url.rstrip("/")

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
    print("  org          {}".format(org_id))
    print("  repo scope   {}".format(args.repo_ids or "every active repo"))
    print("  mode         {}".format("APPLY — this writes" if args.apply else "dry run"))
    print("")
    if not args.apply:
        print("  Dry run: shows exactly what would change. Nothing is")
        print("  written. Re-run with --apply once you're happy with it.")
        print("")

    params = {"dry_run": "false" if args.apply else "true"}
    if args.repo_ids:
        params["repo_ids"] = args.repo_ids
    query = "?" + urllib.parse.urlencode(params)

    result = http(
        "{}/orgs/{}/merge_to_deploy_audit/fix{}".format(analytics, org_id, query),
        method="PUT",
    )

    print("=== Result ===")
    print("  repos checked        {}".format(result.get("repos_checked", 0)))
    print("  repos with changes   {}".format(result.get("repos_with_changes", 0)))
    print("  total changes        {}".format(result.get("total_changes", 0)))
    print("  corrected            {}".format(result.get("corrected", 0)))
    print("  nulled (pending backfill)  {}".format(
        result.get("nulled_pending_backfill", 0)
    ))
    print("")

    changes = result.get("changes") or {}
    if not changes:
        print("Nothing to change. Every cached Release time already agrees")
        print("with a fresh replay of the matching algorithm.")
        return

    for repo_name, repo_changes in changes.items():
        print("--- {} ({} change{}) ---".format(
            repo_name, len(repo_changes), "" if len(repo_changes) == 1 else "s"
        ))
        for c in repo_changes:
            print("  PR #{}  {}".format(c["pr_number"], c["pr_url"]))
            print("    was    {}".format(humanise(c["cached_seconds"])))
            print("    now    {}".format(humanise(c["new_value"])))
            print("    action {}".format(c["action"]))
        print("")

    if args.apply:
        print("Written. These PRs will show the corrected Release time")
        print("immediately — no sync needed, since this wrote the value")
        print("directly rather than waiting for the next one to recompute it.")
    else:
        print("Nothing written — this was a dry run. Re-run with --apply to")
        print("write these changes for real.")


if __name__ == "__main__":
    main()
