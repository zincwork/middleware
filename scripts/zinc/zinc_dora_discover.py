#!/usr/bin/env python3
"""
zinc_dora_discover.py — read-only GitHub discovery for Middleware Option A.

Lists every team in a GitHub org, the repositories each team has access to, and
the members of each team. Writes two files:

  * a JSON config consumed by zinc_dora_apply.py
  * a Markdown report answering the only question that matters before you
    configure anything: will repo-scoped Middleware teams actually produce
    different numbers per team, or will they all look identical?

Read-only. Makes no writes to GitHub and does not touch Middleware.

Requires a GitHub personal access token with the `read:org` scope, supplied via
the GITHUB_TOKEN environment variable. The token is never printed or stored.

Usage:
    export GITHUB_TOKEN=...            # do not paste the token into this file
    python3 zinc_dora_discover.py --org zincwork

Standard library only. No pip install required.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

GITHUB_API = "https://api.github.com"
PAGE_SIZE = 100

# Highest to lowest. A team's effective permission on a repo is the highest
# entry in this list that GitHub reports as true.
PERMISSION_RANK = ["admin", "maintain", "push", "triage", "pull"]


# --------------------------------------------------------------------------
# GitHub plumbing
# --------------------------------------------------------------------------

def _token():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        sys.exit(
            "GITHUB_TOKEN is not set.\n\n"
            "Set it in your shell for this session only:\n"
            "    export GITHUB_TOKEN=<your token>\n\n"
            "The token needs the read:org scope, which is one of the scopes\n"
            "Middleware already requires, so an existing PAT should work."
        )
    return token


def _get(path, params=None):
    """GET a single GitHub API page and return (parsed_json, response_headers)."""
    url = GITHUB_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + _token())
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "zinc-dora-discover")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")[:400]
        if err.code == 401:
            sys.exit("GitHub rejected the token (401). Check GITHUB_TOKEN is current.")
        if err.code == 403 and "rate limit" in body.lower():
            sys.exit("GitHub rate limit hit (403). Wait and re-run.")
        if err.code in (403, 404):
            sys.exit(
                "GitHub returned {} for {}\n\n{}\n\n"
                "The usual cause is a token without the read:org scope, or a token\n"
                "that is not a member of the organisation. Org teams are not\n"
                "readable without read:org.".format(err.code, path, body)
            )
        sys.exit("GitHub returned {} for {}\n\n{}".format(err.code, path, body))
    except urllib.error.URLError as err:
        sys.exit("Could not reach GitHub: {}".format(err.reason))


def _get_all(path, params=None):
    """GET every page of a paginated GitHub collection."""
    out = []
    page = 1
    while True:
        merged = dict(params or {})
        merged.update({"per_page": PAGE_SIZE, "page": page})
        batch, _ = _get(path, merged)
        if not isinstance(batch, list):
            sys.exit("Expected a list from {}, got {}".format(path, type(batch).__name__))
        out.extend(batch)
        if len(batch) < PAGE_SIZE:
            return out
        page += 1
        if page > 100:  # 10,000 items; a safety valve, not an expected path
            sys.exit("Refusing to page beyond 10,000 items from {}".format(path))


def effective_permission(repo):
    """Return the highest permission GitHub reports for this team on this repo."""
    perms = repo.get("permissions") or {}
    for level in PERMISSION_RANK:
        if perms.get(level):
            return level
    # Older GitHub Enterprise versions report role_name instead.
    return repo.get("role_name") or "unknown"


def meets_threshold(permission, threshold):
    try:
        return PERMISSION_RANK.index(permission) <= PERMISSION_RANK.index(threshold)
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Shaping
# --------------------------------------------------------------------------

def repo_entry(repo, permission, deployment_type):
    """Shape a GitHub repo into the form zinc_dora_apply.py expects.

    idempotency_key must be the GitHub numeric repo id as a string. Middleware
    keys OrgRepo rows on exactly that value (see etl_github_handler.py, where
    idempotency_key=str(github_repo.id)), so using it makes the apply step
    attach to the rows your existing sync already created rather than creating
    duplicates.
    """
    default_branch = repo.get("default_branch") or "main"
    return {
        "id": repo["id"],
        "name": repo["name"],
        "default_branch": default_branch,
        "permission": permission,
        "archived": bool(repo.get("archived")),
        # Middleware's own default. Change to WORKFLOW for repos that deploy via
        # GitHub Actions, and set workflow_ids if you do.
        "deployment_type": deployment_type,
        # POSIX regex, anchored. This is the format Middleware stores and the
        # format its branch filters match against.
        "prod_branches": ["^" + default_branch + "$"],
    }


def collect(org, threshold, include_archived, deployment_type):
    print("Reading teams in {} ...".format(org), file=sys.stderr)
    raw_teams = _get_all("/orgs/{}/teams".format(org))
    if not raw_teams:
        sys.exit(
            "No teams found in {}. Either the org has no teams, or the token\n"
            "cannot see them (read:org scope required).".format(org)
        )

    teams = []
    for t in sorted(raw_teams, key=lambda x: x["slug"]):
        slug = t["slug"]
        print("  {} ...".format(slug), file=sys.stderr)

        members = sorted(
            m["login"] for m in _get_all("/orgs/{}/teams/{}/members".format(org, slug))
        )

        repos = []
        skipped_permission = 0
        skipped_archived = 0
        for r in _get_all("/orgs/{}/teams/{}/repos".format(org, slug)):
            perm = effective_permission(r)
            if not meets_threshold(perm, threshold):
                skipped_permission += 1
                continue
            if r.get("archived") and not include_archived:
                skipped_archived += 1
                continue
            repos.append(repo_entry(r, perm, deployment_type))

        teams.append({
            "slug": slug,
            "name": t["name"],
            "parent": (t.get("parent") or {}).get("slug"),
            "middleware_team_name": t["name"],
            "members": members,
            "repos": sorted(repos, key=lambda r: r["name"]),
            "_skipped_below_permission": skipped_permission,
            "_skipped_archived": skipped_archived,
        })

    print("Reading all org repositories ...", file=sys.stderr)
    all_repos = []
    for r in _get_all("/orgs/{}/repos".format(org), {"type": "all"}):
        if r.get("archived") and not include_archived:
            continue
        all_repos.append(repo_entry(r, "n/a", deployment_type))

    return {
        "org": org,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_permission": threshold,
        "include_archived": include_archived,
        "default_deployment_type": deployment_type,
        "teams": teams,
        "all_repos": sorted(all_repos, key=lambda r: r["name"]),
    }


# --------------------------------------------------------------------------
# The report — this is the part that answers "will Option A work?"
# --------------------------------------------------------------------------

def analyse(config):
    """Work out whether repo-scoped teams will be distinguishable."""
    teams = config["teams"]
    repo_to_teams = {}
    for team in teams:
        for repo in team["repos"]:
            repo_to_teams.setdefault(repo["name"], []).append(team["slug"])

    for team in teams:
        names = {r["name"] for r in team["repos"]}
        team["_repo_names"] = names
        team["_exclusive"] = sorted(
            n for n in names if len(repo_to_teams.get(n, [])) == 1
        )

    # Teams whose repo set is exactly the same as another team's will always
    # produce identical DORA figures.
    identical = []
    for i, a in enumerate(teams):
        for b in teams[i + 1:]:
            if a["_repo_names"] and a["_repo_names"] == b["_repo_names"]:
                identical.append((a["slug"], b["slug"]))

    empty = [t["slug"] for t in teams if not t["_repo_names"]]
    no_exclusive = [
        t["slug"] for t in teams if t["_repo_names"] and not t["_exclusive"]
    ]
    shared = sorted(n for n, owners in repo_to_teams.items() if len(owners) > 1)

    return {
        "repo_to_teams": repo_to_teams,
        "identical": identical,
        "empty": empty,
        "no_exclusive": no_exclusive,
        "shared": shared,
    }


def verdict_lines(config, findings):
    teams = [t for t in config["teams"] if t["_repo_names"]]
    lines = []

    if not teams:
        lines.append(
            "**Option A cannot work as configured.** No team has any repository at "
            "the `{}` permission threshold or above. Try a lower threshold with "
            "`--min-permission pull`, or check the token can see team repositories."
            .format(config["min_permission"])
        )
        return lines

    if findings["identical"]:
        pairs = ", ".join("`{}` = `{}`".format(a, b) for a, b in findings["identical"])
        lines.append(
            "**Some teams are indistinguishable.** These pairs have identical "
            "repository sets and will therefore show identical DORA figures, every "
            "time, no matter what: {}. Option A cannot separate them.".format(pairs)
        )

    if findings["no_exclusive"]:
        lines.append(
            "**No exclusive repositories:** {}. Every repository these teams touch "
            "is also assigned to another team, so their numbers are a re-cut of the "
            "same underlying data rather than a measure of that team's work."
            .format(", ".join("`" + s + "`" for s in findings["no_exclusive"]))
        )

    if findings["empty"]:
        lines.append(
            "**Empty teams:** {}. These have no repositories at the `{}` threshold "
            "and will render an empty dashboard in Middleware. Consider excluding "
            "them, or lowering the threshold."
            .format(
                ", ".join("`" + s + "`" for s in findings["empty"]),
                config["min_permission"],
            )
        )

    exclusive_counts = [len(t["_exclusive"]) for t in teams]
    if all(c > 0 for c in exclusive_counts) and not findings["identical"]:
        lines.append(
            "**Option A looks viable.** Every non-empty team has at least one "
            "repository exclusive to it, so per-team figures will differ. Proceed "
            "to `zinc_dora_apply.py`."
        )
    elif not findings["identical"] and not findings["no_exclusive"]:
        lines.append(
            "**Option A looks broadly viable**, with the caveats above."
        )
    else:
        lines.append(
            "**Recommendation:** Option A will give you a working org-wide view "
            "(the \"All Zinc\" team) but weak per-team separation. If per-team "
            "numbers are the point, this is the evidence for moving to Option B "
            "— filtering by the people in each team rather than the repositories."
        )

    return lines


def write_report(path, config, findings):
    teams = config["teams"]
    out = []
    w = out.append

    w("# GitHub team / repository discovery — {}".format(config["org"]))
    w("")
    w("Generated {} by `zinc_dora_discover.py`.".format(config["generated_at"]))
    w("")
    w("Permission threshold: `{}` or higher. Archived repositories {}."
      .format(config["min_permission"],
              "included" if config["include_archived"] else "excluded"))
    w("")

    w("## Verdict")
    w("")
    for line in verdict_lines(config, findings):
        w("- " + line)
    w("")

    w("## Teams")
    w("")
    w("| Team | Slug | Members | Repos | Exclusive repos | Below threshold |")
    w("|---|---|---:|---:|---:|---:|")
    for t in teams:
        w("| {} | `{}` | {} | {} | {} | {} |".format(
            t["name"], t["slug"], len(t["members"]), len(t["repos"]),
            len(t["_exclusive"]), t["_skipped_below_permission"],
        ))
    w("")
    w("Org-wide: {} repositories in total (the \"All Zinc\" team).".format(
        len(config["all_repos"])))
    w("")

    if findings["shared"]:
        w("## Repositories shared across teams")
        w("")
        w("These are the reason per-team figures may not separate cleanly.")
        w("")
        w("| Repository | Teams |")
        w("|---|---|")
        for name in findings["shared"]:
            w("| `{}` | {} |".format(
                name,
                ", ".join("`" + s + "`" for s in findings["repo_to_teams"][name]),
            ))
        w("")

    w("## Per-team detail")
    w("")
    for t in teams:
        w("### {} (`{}`)".format(t["name"], t["slug"]))
        w("")
        if t["parent"]:
            w("Child team of `{}`.".format(t["parent"]))
            w("")
        w("Members ({}): {}".format(
            len(t["members"]),
            ", ".join("`" + m + "`" for m in t["members"]) if t["members"] else "_none_",
        ))
        w("")
        if not t["repos"]:
            w("_No repositories at this threshold._")
            w("")
            continue
        w("| Repository | Permission | Default branch | Exclusive |")
        w("|---|---|---|---|")
        for r in t["repos"]:
            w("| `{}` | {} | `{}` | {} |".format(
                r["name"], r["permission"], r["default_branch"],
                "yes" if r["name"] in t["_exclusive"] else "no",
            ))
        w("")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def write_config(path, config):
    """Write the apply-script config, stripping the report-only bookkeeping keys."""
    clean = dict(config)
    clean["teams"] = []
    for t in config["teams"]:
        clean["teams"].append({
            k: v for k, v in t.items() if not k.startswith("_")
        })
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2, sort_keys=False)
        fh.write("\n")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Read-only GitHub team/repo discovery for Middleware Option A.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--org", required=True, help="GitHub org login, e.g. zincwork")
    ap.add_argument(
        "--min-permission", default="push", choices=PERMISSION_RANK,
        help="Treat a repo as belonging to a team only at this permission or "
             "higher (default: push). Use 'pull' to include read-only access, "
             "which will usually make every team look like it owns everything.",
    )
    ap.add_argument("--include-archived", action="store_true",
                    help="Include archived repositories (excluded by default).")
    ap.add_argument(
        "--deployment-type", default="PR_MERGE", choices=["PR_MERGE", "WORKFLOW"],
        help="Default deployment type written into the config for every repo "
             "(default: PR_MERGE). Edit per repo in the JSON afterwards if some "
             "repos deploy via GitHub Actions.",
    )
    ap.add_argument("--out", default="zinc_teams.json", help="Config output path.")
    ap.add_argument("--report", default="zinc_teams_report.md", help="Report output path.")
    args = ap.parse_args()

    config = collect(args.org, args.min_permission,
                     args.include_archived, args.deployment_type)
    findings = analyse(config)

    write_report(args.report, config, findings)
    write_config(args.out, config)

    print("", file=sys.stderr)
    print("Wrote {} and {}".format(args.out, args.report), file=sys.stderr)
    print("", file=sys.stderr)
    for line in verdict_lines(config, findings):
        # Strip the markdown emphasis for terminal output.
        print("  " + line.replace("**", ""), file=sys.stderr)


if __name__ == "__main__":
    main()
