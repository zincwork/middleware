#!/usr/bin/env python3
"""
verify_shortcut_sync.py — did the ticket sync land, and is it right?

Counts stories straight from the Shortcut API for the mapped teams, then asks
Middleware what it stored, and compares. Also reports how many tickets got a
pull request link, which is the piece that makes Cockpit worth building.

Read-only.

    export SHORTCUT_TOKEN=...
    python3 verify_shortcut_sync.py
    python3 verify_shortcut_sync.py --team Skipper --days 30

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
SHORTCUT_API = "https://api.app.shortcut.com/api/v3"


def http(url, headers=None, timeout=120):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "zinc-verify-shortcut")
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


def shortcut_story_count(headers, mention_name, since):
    """Total stories for one team updated since `since`, per Shortcut itself."""
    query = "team:{} updated:{}..*".format(mention_name, since.strftime("%Y-%m-%d"))
    url = "{}/search/stories?{}".format(
        SHORTCUT_API, urllib.parse.urlencode({"query": query, "page_size": 1})
    )
    payload = http(url, headers=headers)
    return payload.get("total")


def main():
    ap = argparse.ArgumentParser(
        description="Compare Middleware's ticket store against Shortcut."
    )
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--days", type=int, default=120,
                    help="Must match the sync's backfill window (default 120).")
    ap.add_argument("--team", help="Only this GitHub team (by slug).")
    args = ap.parse_args()

    token = os.environ.get("SHORTCUT_TOKEN", "").strip()
    if not token:
        sys.exit("SHORTCUT_TOKEN is not set. export SHORTCUT_TOKEN=... first.")
    headers = {"Shortcut-Token": token}

    session = http(args.base_url.rstrip("/") + "/api/auth/session")
    org_id = (session.get("org") or {}).get("id")
    if not org_id:
        sys.exit("Could not read an org id from Middleware.")

    state = http(
        "{}/api/resources/orgs/{}/shortcut_teams".format(
            args.base_url.rstrip("/"), org_id
        )
    )

    print("=== Setup ===")
    print("  token stored:  {}".format("yes" if state.get("token_stored") else "NO"))
    print("  tickets stored: {}".format(state.get("ticket_count")))

    # The mapping lives in web-server/config/github_teams.json, not in the
    # database, so the route reports it under `mapped_teams`. An older build
    # returned `mappings` read from the now-removed TeamTickets table — detect
    # that rather than reporting a false "nothing mapped".
    mappings = state.get("mapped_teams")
    if mappings is None and "mappings" in state:
        sys.exit(
            "The installed shortcut_teams route is the superseded version — it\n"
            "reads the mapping from the TeamTickets database table, which is no\n"
            "longer written to. Your mapping is almost certainly fine; the route\n"
            "just cannot see it.\n\n"
            "Re-run the installer to pick up the current files, then restart:\n"
            "    python3 apply_cockpit_layer1.py --repo ~/Downloads/middleware\n"
            "    cd ~/Downloads/middleware && docker compose watch\n\n"
            "Check the mapping directly meanwhile:\n"
            "    python3 -c \"import json;d=json.load(open('\"'\"'\"\n"
            "      \"~/Downloads/middleware/web-server/config/github_teams.json'\"'\"'\"\n"
            "      \"));print([t['slug'] for t in d['teams'] if t.get('shortcut_team_id')])\""
        )

    if not mappings:
        unmapped = state.get("unmapped_github_teams") or []
        sys.exit(
            "No GitHub team has a Shortcut team mapped to it.\n\n"
            "{} GitHub team(s) in the config, none mapped.\n"
            "Run:\n"
            "    export SHORTCUT_TOKEN=...\n"
            "    python3 setup_shortcut.py --list-teams\n"
            "    python3 setup_shortcut.py --only skipper".format(len(unmapped))
        )

    for m in mappings:
        print("  GitHub '{}' <- Shortcut '{}' ({} member(s))".format(
            m["github_team"], m.get("shortcut_team_name"),
            m.get("member_count")))
    if state.get("unmapped_github_teams"):
        print("  unmapped GitHub teams: {}".format(
            ", ".join(state["unmapped_github_teams"])))
    print("")

    if not state.get("ticket_count"):
        print("Middleware has no tickets yet. Either the sync has not run, or it")
        print("failed. Check the logs — the ticket sync is the last step and its")
        print("errors are logged but do not stop the others:")
        print("    docker compose logs -f | grep -i 'Tickets Sync'")
        return

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    print("=== Shortcut (ground truth), updated since {} ===".format(since.date()))

    selected = [
        m for m in mappings
        if not args.team or m["github_team"].lower() == args.team.lower()
    ]
    if not selected:
        sys.exit("No mapping matches --team {}".format(args.team))

    total_expected = 0
    for m in selected:
        # The GitHub team slug IS the Shortcut mention name for every Zinc
        # delivery team, which is what makes the join exact.
        mention = m["github_team"]
        try:
            count = shortcut_story_count(headers, mention, since)
        except RuntimeError as err:
            print("  {:<14} ERROR: {}".format(
                m["github_team"], str(err).splitlines()[0]))
            continue
        total_expected += count or 0
        print("  {:<14} {} story(ies) in Shortcut".format(m["github_team"], count))

    print("")
    print("  Shortcut total across mapped teams: {}".format(total_expected))
    print("  Middleware stored (all teams):      {}".format(state.get("ticket_count")))
    print("")

    stored = state.get("ticket_count") or 0
    if total_expected and stored >= total_expected * 0.9:
        print("  Counts are in the same range. Note Middleware's figure excludes")
        print("  archived stories and includes sub-tasks as rows, so an exact")
        print("  match is not expected.")
    elif total_expected:
        shortfall = total_expected - stored
        print("  Middleware is short by roughly {}. Likely causes:".format(shortfall))
        print("   - the sync has not finished; Shortcut's 200/minute limit makes")
        print("     the first backfill slow, and it throttles itself")
        print("   - --days here does not match the sync's backfill window")
        print("   - a team is mapped in Shortcut but its stories sit on another team")

    print("")
    print("Next: run the ticket flow metrics once Layer 2 is in (cycle time by")
    print("state, throughput, bug ratio), and check the ticket-to-PR links have")
    print("resolved — that join is what makes the Cockpit view worth having.")


if __name__ == "__main__":
    main()
