#!/usr/bin/env python3
"""
make_github_teams_config.py — build the team config the metrics filter reads.

Takes the Option A discovery output and emits, per team:
  * members         — GitHub logins, for author attribution
  * branch_prefixes — branch name prefixes, for branch attribution

The branch prefixes come from Zinc's own CircleCI config, which documents the
mapping:

    # Team names: Blue=Skipper, Red=Red Team, Green=Scallops, Gold=Bliss

In practice the colours are what people actually type — `skipper` and
`scallops` appear once across 568 remote branches — so both aliases are
included for each team.

Input:  zinc_teams.json  (from zinc_dora_discover.py)
Output: web-server/config/github_teams.json

Usage:
    python3 make_github_teams_config.py \
        --in zinc_teams.json \
        --out ~/code/middleware/web-server/config/github_teams.json

Standard library only.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Keys are matched against the team slug first, then the lowercased team name,
# then as a substring of either. Override the whole thing with --prefix-map.
DEFAULT_BRANCH_PREFIXES = {
    "skipper": ["blue", "skipper"],
    "red-team": ["red", "red-team"],
    "red": ["red", "red-team"],
    "scallops": ["green", "scallops"],
    "bliss": ["gold", "bliss"],
}


def guess_branch_prefixes(slug, name, prefix_map):
    slug_l = (slug or "").lower()
    name_l = (name or "").lower()

    if slug_l in prefix_map:
        return prefix_map[slug_l]
    if name_l in prefix_map:
        return prefix_map[name_l]

    # Longest key first so "red-team" wins over "red".
    for key in sorted(prefix_map, key=len, reverse=True):
        if key in slug_l or key in name_l:
            return prefix_map[key]
    return []


def main():
    ap = argparse.ArgumentParser(
        description="Build github_teams.json from zinc_dora_discover.py output."
    )
    ap.add_argument("--in", dest="src", default="zinc_teams.json")
    ap.add_argument(
        "--out",
        dest="dest",
        default="web-server/config/github_teams.json",
        help="Must be web-server/config/github_teams.json inside your "
             "Middleware checkout for the app to find it.",
    )
    ap.add_argument("--skip-empty", action="store_true",
                    help="Omit teams with no members and no branch prefixes.")
    ap.add_argument("--exclude", action="append", default=[], metavar="LOGIN",
                    help="Drop a login from every team. Repeatable. Use for "
                         "bots and service accounts.")
    ap.add_argument("--prefix-map", metavar="PATH",
                    help='JSON file of {"team-slug": ["prefix", ...]} replacing '
                         "the built-in mapping.")
    args = ap.parse_args()

    try:
        with open(args.src, encoding="utf-8") as fh:
            src = json.load(fh)
    except FileNotFoundError:
        sys.exit(
            "Not found: {}\n\n"
            "This is the file zinc_dora_discover.py wrote in Option A. If you no "
            "longer have it, re-run:\n"
            "    export GITHUB_TOKEN=...\n"
            "    python3 ../option-a/zinc_dora_discover.py --org zincwork".format(
                args.src
            )
        )
    except json.JSONDecodeError as err:
        sys.exit("{} is not valid JSON: {}".format(args.src, err))

    prefix_map = DEFAULT_BRANCH_PREFIXES
    if args.prefix_map:
        try:
            with open(args.prefix_map, encoding="utf-8") as fh:
                prefix_map = {k.lower(): v for k, v in json.load(fh).items()}
        except (OSError, json.JSONDecodeError) as err:
            sys.exit("Could not read {}: {}".format(args.prefix_map, err))

    excluded = {login.lower() for login in args.exclude}
    teams = []
    for team in src.get("teams", []):
        members = [
            m for m in (team.get("members") or []) if m.lower() not in excluded
        ]
        prefixes = guess_branch_prefixes(
            team.get("slug"), team.get("name"), prefix_map
        )
        if args.skip_empty and not members and not prefixes:
            continue
        teams.append({
            "slug": team["slug"],
            "name": team.get("name") or team["slug"],
            "members": sorted(members),
            "branch_prefixes": prefixes,
        })

    if not teams:
        sys.exit("No teams to write. Check {} and --skip-empty.".format(args.src))

    dest_dir = os.path.dirname(os.path.abspath(args.dest))
    if not os.path.isdir(dest_dir):
        sys.exit(
            "Directory does not exist: {}\n\n"
            "Check --out points inside your Middleware checkout, e.g.\n"
            "    ~/code/middleware/web-server/config/github_teams.json"
            .format(dest_dir)
        )

    with open(args.dest, "w", encoding="utf-8") as fh:
        json.dump({
            "org": src.get("org", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": os.path.abspath(args.src),
            "teams": teams,
        }, fh, indent=2)
        fh.write("\n")

    print("Wrote {}".format(args.dest))
    print("")
    print("  {:<20} {:>8}  {}".format("team", "members", "branch prefixes"))
    for team in teams:
        print("  {:<20} {:>8}  {}".format(
            team["slug"], len(team["members"]),
            ", ".join(team["branch_prefixes"]) or "(none — branch attribution "
                                                 "will return nothing)",
        ))
    print("")

    no_prefix = [t["slug"] for t in teams if not t["branch_prefixes"]]
    if no_prefix:
        print("No branch prefixes guessed for: {}".format(", ".join(no_prefix)))
        print("Branch attribution will find nothing for those teams. Supply a")
        print("mapping with --prefix-map if the convention differs, e.g.")
        print('    {"' + no_prefix[0] + '": ["someprefix"]}')
        print("")

    overlaps = {}
    for team in teams:
        for member in team["members"]:
            overlaps.setdefault(member, []).append(team["slug"])
    multi = {m: t for m, t in overlaps.items() if len(t) > 1}
    if multi:
        print("These people are in more than one team, so their PRs count")
        print("towards each:")
        for member, slugs in sorted(multi.items()):
            print("  {} -> {}".format(member, ", ".join(slugs)))


if __name__ == "__main__":
    main()
