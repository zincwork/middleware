#!/usr/bin/env python3
"""
audit_merge_to_deploy.py — find PRs whose cached "Release" time disagrees
with what the real matching algorithm would produce today.

Why this exists
----------------
merge_to_deploy ("Release" in the UI) is cached permanently on each PR: once
set, no later sync ever looks at it again. The CircleCI timestamp-parsing
bug (fixed in mhq/exapi/circle_ci.py, 2026-09-29) silently dropped some real
deployments before they were recorded, so PRs merged around those gaps got
matched to whatever deployment the matcher found next — in one observed
case (zincwork/mvp-api#6679), 4 weeks 5 days later than the deploy that
actually shipped them, because the matcher has no distance bound at all.

Backfilling the missing deployment history fixes future syncs; it does
nothing for a PR already cached against the wrong one. This calls a
read-only endpoint that replays the real matching algorithm
(DeploymentPRMapperService — reused, not reimplemented) against the current
deployment history and reports every disagreement.

This tool never writes anything. It cannot fix a flagged PR — only report
it. Two kinds of finding come back:

  mismatch                          A deploy now exists that the PR should
                                     have matched instead of the one it's
                                     cached against. Fixable once you're
                                     ready: null merge_to_deploy for that PR
                                     so the real cache handler recomputes it
                                     on the next sync.

  cached_value_has_no_replay_match  The PR has a cached value, but no
                                     deployment it can reach exists at all
                                     yet. The underlying deploy is still
                                     missing — widen the sync window first
                                     (see backfill_tickets.py, which resets
                                     the same shared bookmarks CircleCI/
                                     workflow sync uses) and re-run this.

    python3 audit_merge_to_deploy.py --dry-run
    python3 audit_merge_to_deploy.py
    python3 audit_merge_to_deploy.py --repo-ids <uuid>,<uuid>

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
    req.add_header("User-Agent", "zinc-audit-merge-to-deploy")
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
        return "—"
    return str(timedelta(seconds=seconds))


def main():
    parser = argparse.ArgumentParser(
        description="Find PRs whose cached Release time disagrees with a "
        "fresh replay of the real matching algorithm."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--analytics-url", default=ANALYTICS_URL)
    parser.add_argument(
        "--repo-ids",
        help="Comma-separated OrgRepo UUIDs to limit the audit to. Default: "
        "every active repo in the org (can be slow on a large org — there "
        "is no lookback window, this replays full history).",
    )
    parser.add_argument("--dry-run", action="store_true")
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
    print("  read-only    yes — no PR, deployment or bookmark row is written")
    print("")

    if args.dry_run:
        print("Dry run — nothing called. Drop --dry-run to run the audit.")
        return

    query = ""
    if args.repo_ids:
        query = "?" + urllib.parse.urlencode({"repo_ids": args.repo_ids})

    result = http("{}/orgs/{}/merge_to_deploy_audit{}".format(analytics, org_id, query))

    print("=== Result ===")
    print(
        "  repos checked        {}".format(result.get("repos_checked", 0))
    )
    print(
        "  repos with findings  {}".format(result.get("repos_with_findings", 0))
    )
    print("  total findings       {}".format(result.get("total_findings", 0)))
    print("")

    findings = result.get("findings") or {}
    if not findings:
        print("Nothing flagged. Every cached Release time agrees with a fresh")
        print("replay of the matching algorithm against current deploy history.")
        return

    for repo_name, repo_findings in findings.items():
        print("--- {} ({} finding{}) ---".format(
            repo_name, len(repo_findings), "" if len(repo_findings) == 1 else "s"
        ))
        for f in repo_findings:
            print("  PR #{}  {}".format(f["pr_number"], f["pr_url"]))
            print("    merged           {}".format(f["state_changed_at"]))
            print("    cached Release   {}".format(humanise(f["cached_seconds"])))
            if f["status"] == "mismatch":
                print("    should be        {}  (deploy {} at {})".format(
                    humanise(f["recomputed_seconds"]),
                    f["recomputed_run_id"],
                    f["recomputed_run_conducted_at"],
                ))
                print("    status           mismatch — a nearer deploy now exists")
            else:
                print("    status           cached_value_has_no_replay_match")
                print("                     — no deploy this PR reaches exists yet;")
                print("                     widen the sync window and re-run")
        print("")

    print("Nothing has been changed. Fixing a flagged PR means nulling its")
    print("merge_to_deploy so the real cache handler recomputes it on the")
    print("next sync — a deliberate follow-up step, not part of this audit.")


if __name__ == "__main__":
    main()
