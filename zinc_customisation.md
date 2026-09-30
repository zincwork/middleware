# Zinc Customisation

> The Github and CircleCI intergrations rely on personal access tokens. These expire in 2 years and are tied to an individual.

Clone a copy of the repository. Then run the setup and run scripts.
dev.sh will launch a docker container.

```
./local-setup.sh # Optional; See note
./dev.sh
```

## Teams
When teams change in Github we need to run some manual updates.
This is the list shown in the main UI for selecting a team.

> You will need your GITHUB_TOKEN handy!

1. Go to https://github.com/settings/tokens and choose Tokens (classic) then Generate new token (classic). The Middleware modal links to this page and labels it "Generate new classic token". Fine-grained tokens will not work, because they do not return the x-oauth-scopes header that the validation reads.
2. Give it a note such as middleware-dora and set an expiry. Anything short means re-issuing the token and re-linking the integration, so pick a window you are happy to maintain.
3. Tick the scopes:
repo (the parent box, which selects all its children)
workflow
read:org, the child box under admin:org
read:user, the child box under user
4. Generate the token and copy it immediately.
> Save this in 1Password or similar, you'll need it from time to time.
5. In Middleware at http://localhost:3333, open the GitHub integration card, paste the token and confirm. Leave Custom domain blank for github.com. For GitHub Enterprise Server enter the base host, for example https://github.mycompany.com, and Middleware appends /api/v3 itself.

```
cd <SOURCE_CODE>
export GITHUB_TOKEN=...
python3 /scripts/zinc/zinc_dora_discover.py --org zincwork
```
Writes two files:

zinc_teams.json — the config the next script consumes
zinc_teams_report.md — the report you actually want to read

Once you are happy generate the json file the UI will use.

```python3 make_github_teams_config.py --in /scripts/zinc/zinc_teams.json \
    --out web-server/config/github_teams.json
```

## CircleCI
In order to get the Deploy time we need to add some further customisation.

> You will need your CIRCLECI_TOKEN handy!

```
cd <SOURCE_CODE>
python3 /scripts/zinc/apply_circleci.py --repo . --dry-run
python3 /scripts/zinc/apply_circleci.py --repo .

export CIRCLECI_TOKEN=...
python3 /scripts/zinc/setup_circleci.py


docker compose watch && curl -X POST http://localhost:9697/sync
python3 /scripts/zinc/verify_circleci.py
```

You get a table like this, and then a verdict:

| Team      | Repos | PRs | Lead time (h) | Deploys | /day | CFR % | Incidents | MTTR (h) |
|-----------|------:|----:|--------------:|--------:|-----:|------:|----------:|---------:|
| All Zinc  |    14 | 132 |          38.4 |      61 | 2.03 |   4.9 |         3 |      6.2 |
| Skipper   |     3 |  41 |          52.1 |      18 | 0.60 |   5.6 |         1 |      9.0 |

### Checking the Release numbers are right
"Release" (merge-to-deploy) is cached permanently on each PR the first time
it's matched to a deployment — once set, no later sync ever revisits it. A
real incident showed why that matters: a CircleCI timestamp-parsing bug
(fixed 2026-09-29, see `mhq/exapi/circle_ci.py`) silently dropped some real
deployments before they were recorded, so PRs merged around those gaps got
cached against whatever deployment came next instead — in one case
(zincwork/mvp-api#6679) 4 weeks 5 days later than the deploy that actually
shipped it, because the matcher has no distance bound at all. Backfilling
the missing deploy history fixes that going forward but does nothing for a
PR already cached wrong.

```
python3 scripts/zinc/audit_merge_to_deploy.py --dry-run
python3 scripts/zinc/audit_merge_to_deploy.py
```

Read-only — it replays the real matching algorithm against current deploy
history and reports any PR whose cached Release time disagrees, it never
writes anything. Two kinds of finding: `mismatch` (a nearer deploy now
exists) and `cached_value_has_no_replay_match` (the real deploy still
hasn't been recovered — widen the sync window first, same mechanism as
`backfill_tickets.py`, then re-run). Worth running after any large CircleCI
backfill, or if a Release number looks implausible.

To correct what it finds:

```
python3 scripts/zinc/fix_merge_to_deploy.py            # dry run by default
python3 scripts/zinc/fix_merge_to_deploy.py --apply
```

`mismatch` PRs are written with the replay's own recomputed value directly —
the same matcher the real cache handler uses, already computed once by the
audit, not a second calculation that could disagree with it — persisted
through the identical `CodeRepoService.update_prs` method the real handler
itself calls. `cached_value_has_no_replay_match` PRs are set to NULL rather
than left wrong, which also makes them eligible for the real handler to pick
up on its own once the missing deploy is backfilled. Always dry-run first;
`--apply` is the only thing that writes.

## Shortcut and the Cockpit view
Ticket flow metrics (cycle time, throughput, bug ratio, WIP, epics) live on the
**Cockpit** page, off the main menu next to DORA Metrics. It reads from
Shortcut, mapped onto the same GitHub teams as everything else above — a
Shortcut team's `mention_name` is matched against the GitHub team `slug`
(exact for skipper, bliss, red-team, scallops, tech-ops, integrations; use
`--map` for anything that doesn't line up).

> You will need your SHORTCUT_TOKEN handy! Create a read-only one at
> https://app.shortcut.com/settings/account/api-tokens

```
cd <SOURCE_CODE>
export SHORTCUT_TOKEN=...
python3 scripts/zinc/setup_shortcut.py --list-teams          # see the join first
python3 scripts/zinc/setup_shortcut.py --only skipper --dry-run
python3 scripts/zinc/setup_shortcut.py --only skipper
```

This writes `shortcut_team_id` into `web-server/config/github_teams.json`
alongside each team's `members`/`branch_prefixes`. Repeat with `--only` per
team, or drop it to map every team the join recognises in one go.

Trigger a sync as usual (`curl -X POST http://localhost:9697/sync`), then
check tickets actually landed and the metrics look sane:

```
python3 scripts/zinc/verify_shortcut_sync.py --days 120
python3 scripts/zinc/verify_ticket_flow.py --team skipper --days 90
```

`preflight_check.py --snapshot before.json` / `--compare before.json` is what
proved installing ticket ingestion didn't move any existing DORA figure — a
before/after snapshot, not something you need for day-to-day mapping. Re-run
it the same way if you ever reinstall or upgrade that layer.

## Managers
Each GitHub team can carry a manager, which is what powers the roll-up view
(one manager, several teams, combined — not averaged — figures).

```
python3 scripts/zinc/set_managers.py --config web-server/config/github_teams.json --dry-run
python3 scripts/zinc/set_managers.py --config web-server/config/github_teams.json
```

Built-in defaults are in the script; override or add one with
`--set skipper="Alisdair Little"` (repeatable), or remove one with
`--clear <team>`. Like the Shortcut mapping above, this survives a re-run of
`make_github_teams_config.py` — that script only ever adds `members` and
`branch_prefixes`, never touches `shortcut_team_id` or `manager`.

Check the combined figures came out right with:

```
python3 scripts/zinc/verify_rollup.py --manager "Alisdair Little" --days 90
```

## Loading earlier ticket history
By default a first sync only reaches back 120 days of Shortcut tickets. To
pull in more history, first raise the org's **Sync Days** setting (Settings
page, capped at 366) and rewind the ticket watermark, then re-sync — in that
order, since raising the setting alone does nothing once a bookmark exists:

```
python3 scripts/zinc/backfill_tickets.py --months 12 --dry-run
python3 scripts/zinc/backfill_tickets.py --months 12
```

This needs Layer 5 installed first (`mhq/service/tickets/bookmark.py` and the
raised interval cap on the ticket API) — check
`git log --oneline -- backend/analytics_server/mhq/service/tickets/bookmark.py`
if you're not sure it's in. The installer for that layer carries its own
payload of new/replaced files and isn't checked into `scripts/zinc/`; it lives
at `~/Claude/Projects/Coding Advice/cockpit-layer5/apply_cockpit_layer5.py`
and is run with `--repo` pointing at this checkout. Ask Al if you need it and
can't find it.

The Shortcut search API filters on when a story was last touched, not when it
was created, so a backfilled "throughput" figure for an old period is a floor,
not an exact count: a story dormant for longer than the window won't surface.

## Sync
By default the schedule lives in setup_utils/cronjob.txt:

`# Every 30 minutes, run the sync script
*/30 * * * * curl -X POST http://localhost:9697/sync >> /var/log/cron/cron.log 2>&1`

You can trigger a sync from your browser.
`curl -X POST http://localhost:9697/sync`