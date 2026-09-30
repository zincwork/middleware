"""
Audit and fix for the merge-to-deploy ("Release" stage) cache.

Why this exists
----------------
`mtd_handler.MergeToDeployCacheHandler` caches `PullRequest.merge_to_deploy`
permanently: `CodeRepoService.get_prs_in_repo_merged_before_given_date_with_merge_to_deploy_as_null`
only ever considers PRs where that column is still NULL. Once a PR is
matched to a deployment — correctly or not — no later sync, incremental or
full, ever looks at it again.

That is fine so long as every real deployment is recorded before the PR
matching runs. It is not fine when a real deployment is silently dropped —
which is exactly what the CircleCI timestamp-parsing bug (fixed in
mhq/exapi/circle_ci.py on 2026-09-29) did: a job whose started_at failed to
parse was excluded from RepoWorkflowRuns entirely, so PRs that should have
matched that deployment instead fell through to whatever deployment the
matcher found next — however much later that was, since the matcher (see
DeploymentPRMapperService.get_all_prs_deployed) has no distance or sanity
bound on how far a match can be.

`replay_merge_to_deploy` finds every PR whose cached value disagrees with a
fresh replay of that exact same matching algorithm, from the start of each
repo's history. `MergeToDeployFixService` acts on those findings:

  mismatch                          A deployment the PR actually reaches
                                     exists in the data right now — write
                                     `replay`'s own recomputed value directly
                                     (same matcher, same arithmetic the real
                                     cache handler would use; not a second,
                                     possibly-diverging implementation).

  cached_value_has_no_replay_match  No deployment this PR reaches exists in
                                     the data at all yet — there is no
                                     correct value to write. Null it instead:
                                     an honest "not yet known" beats a
                                     confirmed-wrong number, and nulling is
                                     exactly what makes the PR eligible for
                                     the real cache handler to compute once
                                     the missing deployment is backfilled.

Every write goes through `CodeRepoService.update_prs` — the identical method
`MergeToDeployCacheHandler` itself calls — so there is exactly one code path
that ever persists this column, not two that could quietly diverge.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import defer

from mhq.service.deployments import DeploymentPRMapperService
from mhq.store import db
from mhq.store.models.code import (
    OrgRepo,
    PullRequest,
    RepoWorkflow,
    RepoWorkflowRuns,
    RepoWorkflowRunsStatus,
)
from mhq.store.models.code.enums import PullRequestState
from mhq.store.repos.code import CodeRepoService


@dataclass
class MergeToDeployFinding:
    pr_id: str
    pr_number: str
    pr_url: str
    state_changed_at: datetime
    cached_seconds: Optional[int]
    recomputed_seconds: Optional[int]
    recomputed_run_id: Optional[str]
    recomputed_run_conducted_at: Optional[datetime]
    status: str  # "mismatch" | "cached_value_has_no_replay_match"


def replay_merge_to_deploy(
    runs: List[RepoWorkflowRuns],
    merged_prs: List[PullRequest],
    deployment_pr_mapper_service: DeploymentPRMapperService,
) -> List[MergeToDeployFinding]:
    """The actual audit logic, kept free of any DB access so it can be
    tested against plain fixture objects instead of a live database.

    `runs` must be every SUCCESS RepoWorkflowRuns for one repo, ascending by
    conducted_at. `merged_prs` must be every merged PullRequest for that
    repo. Both need only the attributes DeploymentPRMapperService and this
    function actually read (id, conducted_at, head_branch for runs; id,
    number, url, state, base_branch, head_branch, state_changed_at,
    merge_to_deploy for PRs) — plain namespaces work fine in tests.
    """
    # Every merged PR starts unmatched, regardless of what is currently
    # cached on it — this is a full replay, not an incremental one.
    unmatched: Dict[str, PullRequest] = {str(pr.id): pr for pr in merged_prs}
    recomputed: Dict[str, Dict] = {}

    for run in runs:
        if not unmatched:
            break
        candidates = [
            pr for pr in unmatched.values() if pr.state_changed_at <= run.conducted_at
        ]
        if not candidates:
            continue
        matched = deployment_pr_mapper_service.get_all_prs_deployed(candidates, run)
        for pr in matched:
            unmatched.pop(str(pr.id), None)
            recomputed[str(pr.id)] = {
                "seconds": int(
                    (run.conducted_at - pr.state_changed_at).total_seconds()
                ),
                "run_id": str(run.id),
                "run_conducted_at": run.conducted_at,
            }

    findings: List[MergeToDeployFinding] = []
    for pr in merged_prs:
        cached = pr.merge_to_deploy
        entry = recomputed.get(str(pr.id))

        if entry is None:
            if cached is not None:
                # The replay found no deployment this PR reaches at all, yet
                # it carries a cached value — the underlying deploy data is
                # still incomplete for this one. A backfill, not a
                # null-and-recompute, is what closes this gap.
                findings.append(
                    MergeToDeployFinding(
                        pr_id=str(pr.id),
                        pr_number=pr.number,
                        pr_url=pr.url,
                        state_changed_at=pr.state_changed_at,
                        cached_seconds=cached,
                        recomputed_seconds=None,
                        recomputed_run_id=None,
                        recomputed_run_conducted_at=None,
                        status="cached_value_has_no_replay_match",
                    )
                )
            continue

        if cached is None:
            # Not yet cached at all — the real cache handler's own next sync
            # will pick this one up normally. Nothing here is wrong; flagging
            # it would just be noise on every still-pending PR.
            continue

        if cached != entry["seconds"]:
            findings.append(
                MergeToDeployFinding(
                    pr_id=str(pr.id),
                    pr_number=pr.number,
                    pr_url=pr.url,
                    state_changed_at=pr.state_changed_at,
                    cached_seconds=cached,
                    recomputed_seconds=entry["seconds"],
                    recomputed_run_id=entry["run_id"],
                    recomputed_run_conducted_at=entry["run_conducted_at"],
                    status="mismatch",
                )
            )

    return findings


# ------------------------------------------------------------------
# Queries, shared by the audit and fix services below. Written directly
# against the models rather than reusing CodeRepoService/WorkflowRepoService,
# because every existing method there is scoped to "since a bookmark" or
# "still NULL" — this needs the unfiltered, full-history version of both,
# which does not exist yet and is not otherwise useful outside an audit
# or fix like these.
# ------------------------------------------------------------------


def _get_org_repos(org_id: str, repo_ids: Optional[List[str]]) -> List[OrgRepo]:
    query = db.session.query(OrgRepo).filter(
        OrgRepo.org_id == org_id, OrgRepo.is_active.is_(True)
    )
    if repo_ids:
        query = query.filter(OrgRepo.id.in_(repo_ids))
    return query.all()


def _get_all_successful_runs(repo_id: str) -> List[RepoWorkflowRuns]:
    return (
        db.session.query(RepoWorkflowRuns)
        .options(defer(RepoWorkflowRuns.meta))
        .join(RepoWorkflow, RepoWorkflow.id == RepoWorkflowRuns.repo_workflow_id)
        .filter(
            RepoWorkflow.org_repo_id == repo_id,
            RepoWorkflow.is_active.is_(True),
            RepoWorkflowRuns.status == RepoWorkflowRunsStatus.SUCCESS,
        )
        .order_by(RepoWorkflowRuns.conducted_at)
        .all()
    )


def _get_all_merged_prs(repo_id: str) -> List[PullRequest]:
    return (
        db.session.query(PullRequest)
        .options(defer(PullRequest.data), defer(PullRequest.meta))
        .filter(
            PullRequest.repo_id == repo_id,
            PullRequest.state == PullRequestState.MERGED,
        )
        .order_by(PullRequest.state_changed_at)
        .all()
    )


class MergeToDeployAuditService:
    def __init__(self):
        self.deployment_pr_mapper_service = DeploymentPRMapperService()

    def audit_org(self, org_id: str, repo_ids: Optional[List[str]] = None):
        repos = _get_org_repos(org_id, repo_ids)
        findings_by_repo: Dict[str, List[MergeToDeployFinding]] = {}
        for repo in repos:
            findings = self.audit_repo(str(repo.id))
            if findings:
                findings_by_repo[str(repo.id)] = findings
        return repos, findings_by_repo

    def audit_repo(self, repo_id: str) -> List[MergeToDeployFinding]:
        runs = _get_all_successful_runs(repo_id)
        merged_prs = _get_all_merged_prs(repo_id)
        if not merged_prs:
            return []
        return replay_merge_to_deploy(
            runs, merged_prs, self.deployment_pr_mapper_service
        )


def get_merge_to_deploy_audit_service() -> MergeToDeployAuditService:
    return MergeToDeployAuditService()


@dataclass
class MergeToDeployFixOutcome:
    finding: MergeToDeployFinding
    action: str  # "corrected" | "nulled_pending_backfill"
    new_value: Optional[int]


def compute_fix_outcome(finding: MergeToDeployFinding) -> MergeToDeployFixOutcome:
    """Pure decision, kept separate from the write itself so it can be
    tested without a database: what should this finding's value become?

    "mismatch" already carries the correct answer — replay_merge_to_deploy
    computed it with the exact same matcher the real cache handler uses, so
    there is nothing left to decide, only to apply. "no replay match" has no
    correct value to write yet; None is the honest state, and is also what
    makes the PR eligible again for the real handler to pick up once the
    missing deployment is backfilled.
    """
    if finding.status == "mismatch":
        return MergeToDeployFixOutcome(
            finding=finding, action="corrected", new_value=finding.recomputed_seconds
        )
    return MergeToDeployFixOutcome(
        finding=finding, action="nulled_pending_backfill", new_value=None
    )


class MergeToDeployFixService:
    """Writes what MergeToDeployAuditService finds. Every write goes through
    CodeRepoService.update_prs — the identical method MergeToDeployCacheHandler
    itself uses — so there is exactly one code path that ever persists
    PullRequest.merge_to_deploy, not two that could quietly diverge."""

    def __init__(self, deployment_pr_mapper_service=None, code_repo_service=None):
        self.deployment_pr_mapper_service = (
            deployment_pr_mapper_service or DeploymentPRMapperService()
        )
        self.code_repo_service = code_repo_service or CodeRepoService()

    def fix_org(self, org_id: str, repo_ids: Optional[List[str]], dry_run: bool):
        repos = _get_org_repos(org_id, repo_ids)
        outcomes_by_repo: Dict[str, List[MergeToDeployFixOutcome]] = {}
        for repo in repos:
            outcomes = self.fix_repo(str(repo.id), dry_run)
            if outcomes:
                outcomes_by_repo[str(repo.id)] = outcomes
        return repos, outcomes_by_repo

    def fix_repo(
        self, repo_id: str, dry_run: bool
    ) -> List[MergeToDeployFixOutcome]:
        runs = _get_all_successful_runs(repo_id)
        merged_prs = _get_all_merged_prs(repo_id)
        if not merged_prs:
            return []

        findings = replay_merge_to_deploy(
            runs, merged_prs, self.deployment_pr_mapper_service
        )
        if not findings:
            return []

        prs_by_id = {str(pr.id): pr for pr in merged_prs}
        outcomes = [compute_fix_outcome(f) for f in findings]

        if not dry_run:
            prs_to_update = []
            for outcome in outcomes:
                pr = prs_by_id[outcome.finding.pr_id]
                pr.merge_to_deploy = outcome.new_value
                prs_to_update.append(pr)
            self.code_repo_service.update_prs(prs_to_update)

        return outcomes


def get_merge_to_deploy_fix_service() -> MergeToDeployFixService:
    return MergeToDeployFixService()
