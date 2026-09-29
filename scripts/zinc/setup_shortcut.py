#!/usr/bin/env python3
"""
setup_shortcut.py — connect Shortcut and map its teams onto GITHUB teams.

The "Filter by the people in a GitHub team" dropdown is the single team
selector. Behind each GitHub team sits everything needed to scope the whole
view:

    members[]         -> author filter for the PR and DORA metrics
    branch_prefixes[] -> alternative branch-based attribution
    shortcut_team_id  -> team filter for the ticket metrics   <- added here

So this maps Shortcut team -> GitHub team, and writes the result into
web-server/config/github_teams.json next to the member lists. Middleware's own
teams are not involved: a Middleware Team is a group of REPOSITORIES, and
Zinc's squads share repositories — which is the whole reason the GitHub Team
filter exists.

The join is exact: Shortcut's `mention_name` equals the GitHub team `slug` for
every Zinc delivery team (skipper, bliss, red-team, scallops, tech-ops,
integrations). `--map` covers anything that does not line up.

    export SHORTCUT_TOKEN=...
    python3 setup_shortcut.py --list-teams          # see the join first
    python3 setup_shortcut.py --only skipper --dry-run
    python3 setup_shortcut.py --only skipper

The token is read from SHORTCUT_TOKEN. It is sent only to the Shortcut API (to
validate) and to your local Middleware (to store). Never written to a file or
printed.

Standard library only.
"""

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE_URL = "http://localhost:3333"
DEFAULT_CONFIG = os.path.expanduser(
    "~/Downloads/middleware/web-server/config/github_teams.json"
)
SHORTCUT_API = "https://api.app.shortcut.com/api/v3"

# Shortcut teams with no GitHub counterpart worth reporting on. Skipped with a
# note rather than failing; --map overrides.
IGNORE_TEAMS = {"zinc", "vibes", "1up"}


def _token():
    token = os.environ.get("SHORTCUT_TOKEN", "").strip()
    if not token:
        sys.exit(
            "SHORTCUT_TOKEN is not set.\n\n"
            "Create a read-only API token at\n"
            "  https://app.shortcut.com/settings/account/api-tokens\n"
            "then:\n"
            "    export SHORTCUT_TOKEN=<your token>\n\n"
            "It is only sent to Shortcut and to your local Middleware."
        )
    return token


def http(url, method="GET", body=None, headers=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-setup-shortcut")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError("{} {} -> HTTP {}\n{}".format(method, url, err.code, detail))
    except urllib.error.URLError as err:
        raise RuntimeError("Could not reach {}: {}".format(url, err.reason))


# --------------------------------------------------------------------------
# Shortcut
# --------------------------------------------------------------------------

def check_shortcut_token(token):
    headers = {"Shortcut-Token": token}
    try:
        member = http(SHORTCUT_API + "/member", headers=headers)
    except RuntimeError as err:
        sys.exit(
            "Shortcut rejected the token.\n\n{}\n\n"
            "Check it is current at "
            "https://app.shortcut.com/settings/account/api-tokens".format(err)
        )
    print("Shortcut token valid (user: {})".format(
        member.get("mention_name") or member.get("name")))
    return headers


def get_shortcut_teams(headers):
    teams = http(SHORTCUT_API + "/groups", headers=headers)
    active = [t for t in teams if not t.get("archived")]
    print("{} active Shortcut team(s)".format(len(active)))
    return active


# --------------------------------------------------------------------------
# The GitHub team config
# --------------------------------------------------------------------------

def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except FileNotFoundError:
        sys.exit(
            "GitHub team config not found: {}\n\n"
            "This is the file the 'GitHub Team' dropdown reads. Generate it "
            "first:\n"
            "    python3 make_github_teams_config.py \\\n"
            "        --in ~/Downloads/middleware/scripts/zinc/zinc_teams.json \\\n"
            "        --out {}".format(path, path)
        )
    except json.JSONDecodeError as err:
        sys.exit("{} is not valid JSON: {}".format(path, err))

    if not isinstance(config.get("teams"), list):
        sys.exit('{} has no "teams" array'.format(path))
    return config


def parse_map_args(map_args, map_file):
    """Explicit Shortcut-team -> GitHub-team-slug overrides."""
    overrides = {}

    if map_file:
        try:
            with open(map_file, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, json.JSONDecodeError) as err:
            sys.exit("Could not read --map-file {}: {}".format(map_file, err))
        if not isinstance(loaded, dict):
            sys.exit(
                '--map-file must be a JSON object, e.g. '
                '{"Red Team": "red-team", "Integrations": "integrations"}'
            )
        for shortcut_name, github_slug in loaded.items():
            overrides[shortcut_name.strip().lower()] = github_slug.strip()

    for pair in map_args or []:
        if "=" not in pair:
            sys.exit(
                'Bad --map value {!r}. Use --map "Shortcut Team=github-team-slug"'
                .format(pair)
            )
        shortcut_name, github_slug = pair.split("=", 1)
        if not shortcut_name.strip() or not github_slug.strip():
            sys.exit("Bad --map value {!r}. Both sides are required.".format(pair))
        overrides[shortcut_name.strip().lower()] = github_slug.strip()

    return overrides


def match_team(shortcut_team, github_by_slug, github_by_name, overrides):
    """Find the GitHub team slug for a Shortcut team.

    Tried in order:
      1. an explicit --map
      2. Shortcut mention_name == GitHub slug  (the exact join for Zinc)
      3. Shortcut name == GitHub name, case-insensitively
      4. Shortcut name slugified == GitHub slug
    """
    name = (shortcut_team.get("name") or "").strip()
    mention = (shortcut_team.get("mention_name") or "").strip().lower()
    lower = name.lower()

    explicit = overrides.get(lower)
    if explicit:
        return explicit, "--map"

    if mention and mention in github_by_slug:
        return mention, "mention_name == slug"

    if lower in github_by_name:
        return github_by_name[lower], "name match"

    slugified = lower.replace(" ", "-")
    if slugified in github_by_slug:
        return slugified, "slugified name"

    return None, None


def build_mappings(shortcut_teams, config, only, overrides):
    github_by_slug = {t["slug"].lower(): t["slug"] for t in config["teams"]}
    github_by_name = {
        (t.get("name") or t["slug"]).strip().lower(): t["slug"]
        for t in config["teams"]
    }

    mappings, skipped, unmatched = [], [], []
    wanted = {o.strip().lower() for o in (only or [])}

    for team in shortcut_teams:
        name = (team.get("name") or "").strip()
        mention = (team.get("mention_name") or "").strip().lower()
        lower = name.lower()

        if wanted and not ({lower, mention} & wanted):
            continue

        slug, how = match_team(team, github_by_slug, github_by_name, overrides)

        if not slug and lower in IGNORE_TEAMS:
            skipped.append((name, "no GitHub team (override with --map)"))
            continue

        if not slug:
            unmatched.append((name, mention))
            continue

        if slug.lower() not in github_by_slug:
            unmatched.append((name, "--map points at '{}', which is not a "
                                    "GitHub team".format(slug)))
            continue

        mappings.append({
            "github_slug": github_by_slug[slug.lower()],
            "shortcut_team_id": team["id"],
            "shortcut_team_name": name,
            "how": how,
        })

    return mappings, skipped, unmatched


def write_config(path, config, mappings, dry_run):
    """Write shortcut_team_id onto the matched GitHub teams, in place.

    Everything else in the file — members, branch_prefixes — is preserved
    untouched, so this never undoes make_github_teams_config.py.
    """
    by_slug = {m["github_slug"]: m for m in mappings}

    changed = []
    for team in config["teams"]:
        mapping = by_slug.get(team["slug"])
        if not mapping:
            continue
        before = team.get("shortcut_team_id")
        if before == mapping["shortcut_team_id"]:
            continue
        team["shortcut_team_id"] = mapping["shortcut_team_id"]
        team["shortcut_team_name"] = mapping["shortcut_team_name"]
        changed.append((team["slug"], before, mapping["shortcut_team_id"]))

    config["shortcut_mapped_at"] = datetime.now(timezone.utc).isoformat()

    if dry_run:
        print("  would write {} change(s) to {}".format(len(changed), path))
        for slug, before, after in changed:
            print("    {:<14} {} -> {}".format(slug, before or "(none)", after))
        return len(changed)

    # Keep one backup so a bad run is recoverable.
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")

    print("  wrote {} change(s) to {}".format(len(changed), path))
    for slug, before, after in changed:
        print("    {:<14} {} -> {}".format(slug, before or "(none)", after))
    return len(changed)


def print_team_comparison(shortcut_teams, config, overrides):
    github_by_slug = {t["slug"].lower(): t["slug"] for t in config["teams"]}
    github_by_name = {
        (t.get("name") or t["slug"]).strip().lower(): t["slug"]
        for t in config["teams"]
    }
    existing = {
        t["slug"]: t.get("shortcut_team_name") for t in config["teams"]
        if t.get("shortcut_team_id")
    }

    print("Shortcut team        mention_name    -> GitHub team      how")
    print("-" * 74)
    for team in sorted(shortcut_teams, key=lambda t: (t.get("name") or "").lower()):
        name = (team.get("name") or "").strip()
        mention = (team.get("mention_name") or "").strip()
        slug, how = match_team(team, github_by_slug, github_by_name, overrides)
        if slug:
            target, detail = github_by_slug.get(slug.lower(), slug), how
        elif name.lower() in IGNORE_TEAMS:
            target, detail = "(skipped)", "not a delivery team"
        else:
            target, detail = "NO MATCH", "use --map"
        print("{:<21}{:<16}-> {:<18}{}".format(name, mention, target, detail))

    print("")
    print("GitHub teams in the config ({}):".format(len(config["teams"])))
    for team in sorted(config["teams"], key=lambda t: t["slug"]):
        mapped = team.get("shortcut_team_name")
        print("  {:<30} members={:<4}{}".format(
            team["slug"], len(team.get("members") or []),
            "already mapped -> {}".format(mapped) if mapped else ""))

    if existing:
        print("")
        print("Already mapped: {}".format(", ".join(sorted(existing))))

    print("")
    print("To pair anything marked NO MATCH:")
    print('    --map "Shortcut Team=github-team-slug"')


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Connect Shortcut and map its teams onto GitHub teams."
    )
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="Path to web-server/config/github_teams.json "
                         "(default: %(default)s)")
    ap.add_argument("--only", action="append", metavar="TEAM",
                    help="Only map this Shortcut team, by name or mention name. "
                         "Repeatable. Start with --only skipper.")
    ap.add_argument("--map", action="append", metavar="SHORTCUT=GH-SLUG",
                    help='Map explicitly, e.g. --map "Red Team=red-team". '
                         "Repeatable. Wins over every automatic match.")
    ap.add_argument("--map-file", metavar="PATH",
                    help='JSON object of {"Shortcut team": "github-slug"}.')
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true",
                    help="Remove every shortcut_team_id from the config and "
                         "delete the stored token. Tickets are left in place.")
    ap.add_argument("--skip-token", action="store_true",
                    help="Only update the mapping; leave the stored token alone.")
    ap.add_argument("--list-teams", action="store_true",
                    help="Show the proposed join and exit. Start here.")
    args = ap.parse_args()

    config_path = os.path.abspath(os.path.expanduser(args.config))
    config = load_config(config_path)
    print("Config: {}".format(config_path))
    print("GitHub teams in config: {}".format(len(config["teams"])))

    session = http(args.base_url.rstrip("/") + "/api/auth/session")
    org_id = (session.get("org") or {}).get("id")
    if not org_id:
        sys.exit(
            "Could not read an org id from /api/auth/session.\n"
            "Is Middleware running? Check with: docker compose ps"
        )
    url = "{}/api/resources/orgs/{}/shortcut_teams".format(
        args.base_url.rstrip("/"), org_id
    )
    print("Org: {}".format(org_id))
    print("")

    if args.revert:
        removed = [t["slug"] for t in config["teams"] if t.get("shortcut_team_id")]
        for team in config["teams"]:
            team.pop("shortcut_team_id", None)
            team.pop("shortcut_team_name", None)
        if args.dry_run:
            print("  would clear the mapping for: {}".format(
                ", ".join(removed) or "(none)"))
            print("  would DELETE the stored token at {}".format(url))
            return
        if os.path.exists(config_path):
            shutil.copy2(config_path, config_path + ".bak")
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
            fh.write("\n")
        print("Cleared the mapping for: {}".format(", ".join(removed) or "(none)"))
        http(url, method="DELETE", body={"confirm": True})
        print("Removed the stored token. The ticket sync will now no-op.")
        print("Tickets already synced are left in place.")
        return

    overrides = parse_map_args(args.map, args.map_file)

    headers = check_shortcut_token(_token())
    shortcut_teams = get_shortcut_teams(headers)
    print("")

    if args.list_teams:
        print_team_comparison(shortcut_teams, config, overrides)
        return

    mappings, skipped, unmatched = build_mappings(
        shortcut_teams, config, args.only, overrides
    )

    if mappings:
        print("Will map:")
        for m in mappings:
            print("  Shortcut {!r:<16} -> GitHub team {!r:<16} ({})".format(
                m["shortcut_team_name"], m["github_slug"], m["how"]))
    if skipped:
        print("")
        print("Skipping:")
        for name, why in skipped:
            print("  {} — {}".format(name, why))
    if unmatched:
        print("")
        print("No GitHub team found ({}):".format(len(unmatched)))
        for name, detail in unmatched:
            print("  {:<20} (mention_name: {})".format(name, detail or "none"))
        print("")
        print("  GitHub team slugs available:")
        print("    {}".format(", ".join(sorted(
            t["slug"] for t in config["teams"]))))
        print("")
        print('  Fix with: --map "{}=<github-team-slug>"'.format(unmatched[0][0]))

    if not mappings:
        print("")
        sys.exit(
            "Nothing to map.\n\n"
            "Run --list-teams to see the proposed join, then use --map for "
            "anything that does not line up."
        )

    print("")
    if not args.skip_token:
        if args.dry_run:
            print("  would POST the token to {}".format(url))
        else:
            http(url, method="POST", body={"token": _token()})
            print("Stored the Shortcut token")

    write_config(config_path, config, mappings, args.dry_run)

    if args.dry_run:
        print("")
        print("Dry run complete. Re-run without --dry-run to apply.")
        return

    print("")
    print("Done. The 'GitHub Team' dropdown now scopes tickets as well as PRs.")
    print("Trigger a sync and wait for it to finish:")
    print("    curl -X POST http://localhost:9697/sync")
    print("")
    print("The first sync backfills 120 days. Shortcut allows 200 requests a")
    print("minute and each story needs a history call, so expect it to take a")
    print("while and to throttle itself. Then:")
    print("    python3 verify_shortcut_sync.py")


if __name__ == "__main__":
    main()
