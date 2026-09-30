"""
Audit and fix for the merge-to-deploy ("Release" stage) cache.

Registered as its own blueprint so the only change to an existing file is two
lines in app.py, matching the pattern used for the tickets blueprint.

See mhq/service/merge_to_deploy_broker/mtd_audit.py for why this exists and
exactly what it checks. In one line: merge_to_deploy is a permanent,
NULL-gated cache, so a PR matched to the wrong (too-distant) deployment
before a data gap was backfilled stays wrong forever unless something
re-checks it.

Two routes:

  GET  .../merge_to_deploy_audit       Read-only. Replays the real matching
                                        algorithm and reports disagreements.
                                        Never writes.

  PUT  .../merge_to_deploy_audit/fix   Writes. Defaults to dry_run=true even
                                        if the caller forgets the parameter —
                                        a stray or scripted call should never
                                        silently write. Every write goes
                                        through CodeRepoService.update_prs,
                                        the same method the real cache
                                        handler itself uses.
"""

from flask import Blueprint
from voluptuous import All, Coerce, Optional, Schema

from mhq.api.request_utils import boolean_validator, queryschema
from mhq.service.merge_to_deploy_broker.mtd_audit import (
    get_merge_to_deploy_audit_service,
    get_merge_to_deploy_fix_service,
)
from mhq.service.query_validator import get_query_validator

app = Blueprint("merge_to_deploy_audit", __name__)


def _split_csv(value: str):
    return [v for v in (value or "").split(",") if v]


def _finding_json(f):
    return {
        "pr_number": f.pr_number,
        "pr_url": f.pr_url,
        "state_changed_at": f.state_changed_at.isoformat(),
        "cached_seconds": f.cached_seconds,
        "recomputed_seconds": f.recomputed_seconds,
        "recomputed_run_id": f.recomputed_run_id,
        "recomputed_run_conducted_at": (
            f.recomputed_run_conducted_at.isoformat()
            if f.recomputed_run_conducted_at
            else None
        ),
        "status": f.status,
    }


@app.route("/orgs/<org_id>/merge_to_deploy_audit", methods={"GET"})
@queryschema(
    Schema(
        {
            Optional("repo_ids"): All(str, Coerce(_split_csv)),
        }
    ),
)
def get_merge_to_deploy_audit(org_id: str, repo_ids: list = None):
    query_validator = get_query_validator()
    query_validator.org_validator(org_id)

    audit_service = get_merge_to_deploy_audit_service()
    repos, findings_by_repo = audit_service.audit_org(org_id, repo_ids)

    repos_by_id = {str(r.id): r for r in repos}
    total_findings = sum(len(v) for v in findings_by_repo.values())

    return {
        "org_id": org_id,
        "repos_checked": len(repos),
        "repos_with_findings": len(findings_by_repo),
        "total_findings": total_findings,
        "findings": {
            repos_by_id[repo_id].name: [_finding_json(f) for f in findings]
            for repo_id, findings in findings_by_repo.items()
        },
    }


@app.route("/orgs/<org_id>/merge_to_deploy_audit/fix", methods={"PUT"})
@queryschema(
    Schema(
        {
            Optional("repo_ids"): All(str, Coerce(_split_csv)),
            # Defaults true so a call missing the parameter entirely — a
            # stray request, an old script, a copy-pasted curl without it —
            # writes nothing rather than writing everything.
            Optional("dry_run", default=True): All(str, Coerce(boolean_validator)),
        }
    ),
)
def put_merge_to_deploy_audit_fix(
    org_id: str, repo_ids: list = None, dry_run: bool = True
):
    query_validator = get_query_validator()
    query_validator.org_validator(org_id)

    fix_service = get_merge_to_deploy_fix_service()
    repos, outcomes_by_repo = fix_service.fix_org(org_id, repo_ids, dry_run)

    repos_by_id = {str(r.id): r for r in repos}
    total = sum(len(v) for v in outcomes_by_repo.values())
    corrected = sum(
        1
        for outcomes in outcomes_by_repo.values()
        for o in outcomes
        if o.action == "corrected"
    )
    nulled = total - corrected

    return {
        "org_id": org_id,
        "dry_run": dry_run,
        "repos_checked": len(repos),
        "repos_with_changes": len(outcomes_by_repo),
        "total_changes": total,
        "corrected": corrected,
        "nulled_pending_backfill": nulled,
        "changes": {
            repos_by_id[repo_id].name: [
                dict(_finding_json(o.finding), action=o.action, new_value=o.new_value)
                for o in outcomes
            ]
            for repo_id, outcomes in outcomes_by_repo.items()
        },
    }
