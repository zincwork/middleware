#!/usr/bin/env python3
"""
set_managers.py — record who each GitHub team reports to.

GitHub has no concept of a manager, so this is the one piece of the org chart
that has to be stated rather than discovered. It goes into
web-server/config/github_teams.json alongside the member lists and the
Shortcut mapping, so one file drives the whole team picture and re-organising
is a config edit rather than a migration.

The manager is the axis the roll-up runs on, not the team, because one
manager can hold several teams — Lucila holds both Bliss and Integration.

Reads the existing config and rewrites only the `manager` field, so the
Shortcut mapping and the member lists are untouched.

    python3 set_managers.py --config ~/Downloads/middleware/web-server/config/github_teams.json --dry-run
    python3 set_managers.py --config ~/Downloads/middleware/web-server/config/github_teams.json
    python3 set_managers.py --config ... --set tech-ops="Someone Else"
    python3 set_managers.py --config ... --clear scallops

Standard library only. Takes effect without a restart — the app re-reads the
file when its mtime changes.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Supplied by Al Little, September 2026. Keyed on the label as given; each is
# resolved against the real GitHub team slugs below, because the two do not
# always match ("Red" is the slug `red-team`, "Integration" is `integrations`).
DEFAULT_MANAGERS = {
    "skipper": "Alisdair Little",
    "bliss": "Lucila Sanjurjo",
    "red": "Anton Khomchenko",
    "scallops": "Christos Panteli",
    "integration": "Lucila Sanjurjo",
}


def resolve_team(label, teams):
    """Find the one team a label refers to, or explain why it cannot.

    Returns (slug, None) or (None, reason).
    """
    wanted = label.strip().lower()
    by_slug = {t["slug"].lower(): t["slug"] for t in teams}
    by_name = {}
    for team in teams:
        by_name.setdefault((team.get("name") or "").strip().lower(), team["slug"])

    if wanted in by_slug:
        return by_slug[wanted], None
    if wanted in by_name:
        return by_name[wanted], None

    # "Red" for `red-team`, "Integration" for `integrations`. Prefix only —
    # a substring match would let "team" hit half the org.
    prefixed = sorted(
        {
            team["slug"]
            for team in teams
            if team["slug"].lower().startswith(wanted)
            or (team.get("name") or "").strip().lower().startswith(wanted)
        }
    )
    if len(prefixed) == 1:
        return prefixed[0], None
    if len(prefixed) > 1:
        return None, "matches {} teams: {}".format(
            len(prefixed), ", ".join(prefixed)
        )
    return None, "no GitHub team matches"


def main():
    parser = argparse.ArgumentParser(
        description="Set the manager on each GitHub team in github_teams.json."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to web-server/config/github_teams.json in your checkout.",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="TEAM=MANAGER",
        help="Set one team's manager, overriding the built-in list. Repeatable.",
    )
    parser.add_argument(
        "--clear",
        action="append",
        default=[],
        metavar="TEAM",
        help="Remove a team's manager. Repeatable.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = os.path.abspath(os.path.expanduser(args.config))
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except FileNotFoundError:
        sys.exit(
            "Not found: {}\n\n"
            "That path should be web-server/config/github_teams.json inside "
            "your Middleware checkout. Generate it with "
            "make_github_teams_config.py first.".format(path)
        )
    except json.JSONDecodeError as err:
        sys.exit("{} is not valid JSON: {}".format(path, err))

    teams = config.get("teams")
    if not isinstance(teams, list) or not teams:
        sys.exit("{} has no teams array.".format(path))

    wanted = dict(DEFAULT_MANAGERS)
    for pair in args.set:
        if "=" not in pair:
            sys.exit("--set expects TEAM=MANAGER, got: {}".format(pair))
        label, manager = pair.split("=", 1)
        wanted[label.strip().lower()] = manager.strip()
    for label in args.clear:
        wanted[label.strip().lower()] = None

    resolved = {}
    problems = []
    for label, manager in wanted.items():
        slug, reason = resolve_team(label, teams)
        if reason:
            problems.append("  {:<16} {}".format(label, reason))
            continue
        if slug in resolved and resolved[slug] != manager:
            problems.append(
                "  {:<16} two different managers resolve to this team".format(slug)
            )
            continue
        resolved[slug] = manager

    if problems:
        print("Could not resolve some labels to a GitHub team:")
        for line in problems:
            print(line)
        print("")
        print("Teams in the config: {}".format(
            ", ".join(sorted(t["slug"] for t in teams))))
        print("Use --set <slug>=\"<manager>\" with the exact slug to be explicit.")
        print("")
        if not resolved:
            sys.exit(1)

    changes = []
    for team in teams:
        if team["slug"] not in resolved:
            continue
        manager = resolved[team["slug"]]
        before = team.get("manager")
        if before == manager:
            continue
        changes.append((team["slug"], before, manager))
        if not args.dry_run:
            if manager:
                team["manager"] = manager
            else:
                team.pop("manager", None)

    print("=== Managers ===")
    for team in sorted(teams, key=lambda t: t["slug"]):
        pending = resolved.get(team["slug"], team.get("manager"))
        shown = pending if pending else "(none)"
        marker = ""
        if any(c[0] == team["slug"] for c in changes):
            marker = "  <- changed"
        mapped = "" if team.get("shortcut_team_id") else "   [no Shortcut team]"
        print("  {:<16} {}{}{}".format(team["slug"], shown, mapped, marker))
    print("")

    unmanaged = sorted(
        t["slug"]
        for t in teams
        if not (resolved.get(t["slug"]) or t.get("manager"))
        and t.get("shortcut_team_id")
    )
    if unmanaged:
        print("These teams have a Shortcut mapping but no manager, so they")
        print("appear in the per-team view and in no roll-up:")
        for slug in unmanaged:
            print("  {}".format(slug))
        print("")

    # Two teams under one manager is the case the roll-up exists for, so say
    # so plainly rather than leaving it to be discovered.
    by_manager = {}
    for team in teams:
        manager = resolved.get(team["slug"], team.get("manager"))
        if manager:
            by_manager.setdefault(manager, []).append(team["slug"])
    multi = {m: s for m, s in by_manager.items() if len(s) > 1}
    if multi:
        print("Roll-ups covering more than one team:")
        for manager, slugs in sorted(multi.items()):
            print("  {:<20} {}".format(manager, ", ".join(sorted(slugs))))
        print("")

    if not changes:
        print("Nothing to change.")
        return

    if args.dry_run:
        print("Dry run — {} change(s) not written. Drop --dry-run to apply.".format(
            len(changes)))
        return

    config["managers_set_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")

    print("Wrote {} change(s) to {}".format(len(changes), path))
    print("No restart needed — the app re-reads the file when it changes.")


if __name__ == "__main__":
    main()
