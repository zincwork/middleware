# Zinc Customisation

> The Github and CircleCI intergrations rely on personal access tokens. These expire in 2 years and are tied to an individual.

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

`cd <SOURCE_CODE>
export GITHUB_TOKEN=...
python3 /scripts/zinc/zinc_dora_discover.py --org zincwork
Writes two files:

zinc_teams.json — the config the next script consumes
zinc_teams_report.md — the report you actually want to read`

Once you are happy generate the json file the UI will use.

`python3 make_github_teams_config.py --in /scripts/zinc/zinc_teams.json \
    --out web-server/config/github_teams.json`

## CircleCI
In order to get the Deploy time we need to add some further customisation.

> You will need your CIRCLECI_TOKEN handy!

`cd <SOURCE_CODE>
python3 /scripts/zinc/apply_circleci.py --repo . --dry-run
python3 /scripts/zinc/apply_circleci.py --repo .

export CIRCLECI_TOKEN=...
python3 /scripts/zinc/setup_circleci.py


docker compose watch && curl -X POST http://localhost:9697/sync
python3 /scripts/zinc/verify_circleci.py`

You get a table like this, and then a verdict:

| Team      | Repos | PRs | Lead time (h) | Deploys | /day | CFR % | Incidents | MTTR (h) |
|-----------|------:|----:|--------------:|--------:|-----:|------:|----------:|---------:|
| All Zinc  |    14 | 132 |          38.4 |      61 | 2.03 |   4.9 |         3 |      6.2 |
| Skipper   |     3 |  41 |          52.1 |      18 | 0.60 |   5.6 |         1 |      9.0 |

## Sync
By default the schedule lives in setup_utils/cronjob.txt:

`# Every 30 minutes, run the sync script
*/30 * * * * curl -X POST http://localhost:9697/sync >> /var/log/cron/cron.log 2>&1`

You can trigger a sync from your browser.
`curl -X POST http://localhost:9697/sync`