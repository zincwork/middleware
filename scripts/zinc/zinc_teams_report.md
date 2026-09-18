# GitHub team / repository discovery — zincwork

Generated 2026-09-04T11:30:25.560329+00:00 by `zinc_dora_discover.py`.

Permission threshold: `push` or higher. Archived repositories excluded.

## Verdict

- **Some teams are indistinguishable.** These pairs have identical repository sets and will therefore show identical DORA figures, every time, no matter what: `bliss` = `developers`, `bliss` = `red-team`, `bliss` = `scallops`, `bliss` = `skipper`, `developers` = `red-team`, `developers` = `scallops`, `developers` = `skipper`, `engineering` = `tech-ops`, `red-team` = `scallops`, `red-team` = `skipper`, `scallops` = `skipper`. Option A cannot separate them.
- **No exclusive repositories:** `bliss`, `core-platform-collaborators`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops`. Every repository these teams touch is also assigned to another team, so their numbers are a re-cut of the same underlying data rather than a measure of that team's work.
- **Empty teams:** `design`, `gsd-read-only`, `gsd-tech`, `operations`, `operations-admin`, `product`. These have no repositories at the `push` threshold and will render an empty dashboard in Middleware. Consider excluding them, or lowering the threshold.
- **Recommendation:** Option A will give you a working org-wide view (the "All Zinc" team) but weak per-team separation. If per-team numbers are the point, this is the evidence for moving to Option B — filtering by the people in each team rather than the repositories.

## Teams

| Team | Slug | Members | Repos | Exclusive repos | Below threshold |
|---|---|---:|---:|---:|---:|
| Bliss | `bliss` | 7 | 13 | 0 | 1 |
| Core Platform Collaborators | `core-platform-collaborators` | 1 | 1 | 0 | 0 |
| Design | `design` | 4 | 0 | 0 | 3 |
| Developers | `developers` | 20 | 13 | 0 | 1 |
| Engineering | `engineering` | 28 | 11 | 0 | 0 |
| GSD Read-Only | `gsd-read-only` | 4 | 0 | 0 | 0 |
| GSD Tech | `gsd-tech` | 3 | 0 | 0 | 0 |
| Operations | `operations` | 13 | 0 | 0 | 0 |
| Operations-Admin | `operations-admin` | 2 | 0 | 0 | 0 |
| Product | `product` | 5 | 0 | 0 | 3 |
| Red Team | `red-team` | 5 | 13 | 0 | 1 |
| Scallops | `scallops` | 4 | 13 | 0 | 1 |
| Skipper | `skipper` | 6 | 13 | 0 | 1 |
| Tech ops | `tech-ops` | 5 | 11 | 0 | 0 |

Org-wide: 20 repositories in total (the "All Zinc" team).

## Repositories shared across teams

These are the reason per-team figures may not separate cleanly.

| Repository | Teams |
|---|---|
| `gen-ai` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `metabase-queries` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `mono-app` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `mvp-api` | `bliss`, `core-platform-collaborators`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `mvp-app` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `share-code-service` | `bliss`, `developers`, `red-team`, `scallops`, `skipper` |
| `story-omnibus` | `bliss`, `developers`, `red-team`, `scallops`, `skipper` |
| `zinc-mobile-app` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `zinc-pdf2image` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `zinc-report-generation` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `zinc-technical-test` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `zinc-terraform` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |
| `zinc-utils` | `bliss`, `developers`, `engineering`, `red-team`, `scallops`, `skipper`, `tech-ops` |

## Per-team detail

### Bliss (`bliss`)

Child team of `developers`.

Members (7): `RobJLeonard`, `abbiehowell-zinc`, `joedavies25`, `lucilasanjurjo-zinc`, `mohamed-zinc`, `rhodger`, `tinapan-zinc`

| Repository | Permission | Default branch | Exclusive |
|---|---|---|---|
| `gen-ai` | push | `main` | no |
| `metabase-queries` | push | `main` | no |
| `mono-app` | push | `main` | no |
| `mvp-api` | push | `main` | no |
| `mvp-app` | push | `main` | no |
| `share-code-service` | push | `main` | no |
| `story-omnibus` | push | `master` | no |
| `zinc-mobile-app` | push | `main` | no |
| `zinc-pdf2image` | push | `main` | no |
| `zinc-report-generation` | push | `main` | no |
| `zinc-technical-test` | push | `master` | no |
| `zinc-terraform` | push | `main` | no |
| `zinc-utils` | push | `main` | no |

### Core Platform Collaborators (`core-platform-collaborators`)

Members (1): `bianka-nedjalkova`

| Repository | Permission | Default branch | Exclusive |
|---|---|---|---|
| `mvp-api` | push | `main` | no |

### Design (`design`)

Members (4): `helloitshanyi`, `joezincwork`, `oliverjones-ui`, `tom-moore-design`

_No repositories at this threshold._

### Developers (`developers`)

Child team of `engineering`.

Members (20): `RobJLeonard`, `abbiehowell-zinc`, `ak-30`, `alisdairlittle`, `ckpanteli`, `emily-dy-yoon`, `hugo-zinc`, `jackcleary01`, `joedavies25`, `khomch`, `lucilasanjurjo-zinc`, `lukeross`, `mohamed-zinc`, `muhammadali96`, `primlaothamatas`, `rhodger`, `sanjeevvp`, `tinapan-zinc`, `vshleifman`, `zincsfoley`

| Repository | Permission | Default branch | Exclusive |
|---|---|---|---|
| `gen-ai` | push | `main` | no |
| `metabase-queries` | push | `main` | no |
| `mono-app` | push | `main` | no |
| `mvp-api` | push | `main` | no |
| `mvp-app` | push | `main` | no |
| `share-code-service` | push | `main` | no |
| `story-omnibus` | push | `master` | no |
| `zinc-mobile-app` | push | `main` | no |
| `zinc-pdf2image` | push | `main` | no |
| `zinc-report-generation` | push | `main` | no |
| `zinc-technical-test` | push | `master` | no |
| `zinc-terraform` | push | `main` | no |
| `zinc-utils` | push | `main` | no |

### Engineering (`engineering`)

Members (28): `RobJLeonard`, `Zn0Kelvin`, `abbiehowell-zinc`, `ak-30`, `alisdairlittle`, `ckpanteli`, `elenaharan`, `emily-dy-yoon`, `fathiyaabdalla`, `hugo-zinc`, `iliasu-zinc`, `jackcleary01`, `joedavies25`, `khomch`, `lucilasanjurjo-zinc`, `lukeross`, `matthew-wagerfield-zinc`, `mohamed-zinc`, `muhammadali96`, `neilbrooks-cyber`, `primlaothamatas`, `rhodger`, `sanjeevvp`, `shervinm95`, `tinapan-zinc`, `vshleifman`, `zinc-deployment`, `zincsfoley`

| Repository | Permission | Default branch | Exclusive |
|---|---|---|---|
| `gen-ai` | push | `main` | no |
| `metabase-queries` | push | `main` | no |
| `mono-app` | push | `main` | no |
| `mvp-api` | push | `main` | no |
| `mvp-app` | push | `main` | no |
| `zinc-mobile-app` | push | `main` | no |
| `zinc-pdf2image` | push | `main` | no |
| `zinc-report-generation` | push | `main` | no |
| `zinc-technical-test` | push | `master` | no |
| `zinc-terraform` | push | `main` | no |
| `zinc-utils` | push | `main` | no |

### GSD Read-Only (`gsd-read-only`)

Members (4): `RobJLeonard`, `iliasu-zinc`, `pahwzinc`, `sanjeevvp`

_No repositories at this threshold._

### GSD Tech (`gsd-tech`)

Members (3): `gsdrob`, `sanjeevvp`, `sanjeevvp-gsd`

_No repositories at this threshold._

### Operations (`operations`)

Members (13): `Callumtheo`, `Samrcnorman`, `dancourse-zincwork`, `fathiyaabdalla`, `iliasu-zinc`, `jaredd-zinc`, `joelblackburn-ui`, `kirtyadarsh-hue`, `lexfaraimo-boop`, `minatopaloglu`, `natbuxton`, `owais-zinc`, `zhen-lim`

_No repositories at this threshold._

### Operations-Admin (`operations-admin`)

Child team of `operations`.

Members (2): `dancourse-zincwork`, `iliasu-zinc`

_No repositories at this threshold._

### Product (`product`)

Members (5): `GabiZinc`, `Shippable-ls`, `kasiapoza-bot`, `oliverjones-ui`, `rikkivanberkel-cloud`

_No repositories at this threshold._

### Red Team (`red-team`)

Child team of `developers`.

Members (5): `RobJLeonard`, `hugo-zinc`, `khomch`, `lukeross`, `vshleifman`

| Repository | Permission | Default branch | Exclusive |
|---|---|---|---|
| `gen-ai` | push | `main` | no |
| `metabase-queries` | push | `main` | no |
| `mono-app` | push | `main` | no |
| `mvp-api` | push | `main` | no |
| `mvp-app` | push | `main` | no |
| `share-code-service` | push | `main` | no |
| `story-omnibus` | push | `master` | no |
| `zinc-mobile-app` | push | `main` | no |
| `zinc-pdf2image` | push | `main` | no |
| `zinc-report-generation` | push | `main` | no |
| `zinc-technical-test` | push | `master` | no |
| `zinc-terraform` | push | `main` | no |
| `zinc-utils` | push | `main` | no |

### Scallops (`scallops`)

Child team of `developers`.

Members (4): `RobJLeonard`, `ckpanteli`, `muhammadali96`, `primlaothamatas`

| Repository | Permission | Default branch | Exclusive |
|---|---|---|---|
| `gen-ai` | push | `main` | no |
| `metabase-queries` | push | `main` | no |
| `mono-app` | push | `main` | no |
| `mvp-api` | push | `main` | no |
| `mvp-app` | push | `main` | no |
| `share-code-service` | push | `main` | no |
| `story-omnibus` | push | `master` | no |
| `zinc-mobile-app` | push | `main` | no |
| `zinc-pdf2image` | push | `main` | no |
| `zinc-report-generation` | push | `main` | no |
| `zinc-technical-test` | push | `master` | no |
| `zinc-terraform` | push | `main` | no |
| `zinc-utils` | push | `main` | no |

### Skipper (`skipper`)

Child team of `developers`.

Members (6): `RobJLeonard`, `ak-30`, `alisdairlittle`, `emily-dy-yoon`, `jackcleary01`, `zincsfoley`

| Repository | Permission | Default branch | Exclusive |
|---|---|---|---|
| `gen-ai` | push | `main` | no |
| `metabase-queries` | push | `main` | no |
| `mono-app` | push | `main` | no |
| `mvp-api` | push | `main` | no |
| `mvp-app` | push | `main` | no |
| `share-code-service` | push | `main` | no |
| `story-omnibus` | push | `master` | no |
| `zinc-mobile-app` | push | `main` | no |
| `zinc-pdf2image` | push | `main` | no |
| `zinc-report-generation` | push | `main` | no |
| `zinc-technical-test` | push | `master` | no |
| `zinc-terraform` | push | `main` | no |
| `zinc-utils` | push | `main` | no |

### Tech ops (`tech-ops`)

Child team of `engineering`.

Members (5): `elenaharan`, `iliasu-zinc`, `sanjeevvp`, `shervinm95`, `zinc-deployment`

| Repository | Permission | Default branch | Exclusive |
|---|---|---|---|
| `gen-ai` | push | `main` | no |
| `metabase-queries` | push | `main` | no |
| `mono-app` | push | `main` | no |
| `mvp-api` | push | `main` | no |
| `mvp-app` | push | `main` | no |
| `zinc-mobile-app` | push | `main` | no |
| `zinc-pdf2image` | push | `main` | no |
| `zinc-report-generation` | push | `main` | no |
| `zinc-technical-test` | push | `master` | no |
| `zinc-terraform` | push | `main` | no |
| `zinc-utils` | push | `main` | no |

