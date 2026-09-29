"""
Which commits did each workflow deployment ship?

Middleware's default deploy-to-PR mapping is by time: a PR counts as deployed
by the first deployment that starts after it merged. That breaks for
approval-gated deploys (Zinc's CircleCI `deploy-production`): approve an older
pipeline after a newer PR has merged and the time rule says the newer PR
shipped, when only the older commit did.

Every CircleCI deploy run records the commit it deployed (`meta.revision`;
GitHub Actions runs carry `head_sha`). The commits a deploy shipped are exactly
those reachable from its revision but not from the previous deploy's, which is
one GitHub compare call per deploy. This runs as a sync step and stores the
answer on the run as `meta.shipped`:

    {"base": "<previous revision>" | None,
     "status": "ahead" | "behind" | "identical" | "diverged" | "no_previous_deploy",
     "shas": [...] | None,       # None = unknown, fall back to the time rule
     "truncated": bool}

"behind" (redeploying an older revision, i.e. a rollback) ships no new commits.
"""

from typing import Dict, List, Optional

from mhq.exapi.github import GithubApiService, GithubRateLimitExceeded
from mhq.store.models import UserIdentityProvider
from mhq.store.models.code import OrgRepo
from mhq.store.models.code.enums import CodeProvider
from mhq.store.models.code.workflows import RepoWorkflow, RepoWorkflowRuns
from mhq.store.models.code.workflows.enums import RepoWorkflowProviders
from mhq.store.repos.code import CodeRepoService
from mhq.store.repos.core import CoreRepoService
from mhq.store.repos.workflows import WorkflowRepoService
from mhq.utils.github import get_custom_github_domain
from mhq.utils.log import LOG

NO_PREVIOUS_DEPLOY = "no_previous_deploy"


def get_run_revision(run: RepoWorkflowRuns) -> Optional[str]:
    meta = run.meta or {}
    return meta.get("revision") or meta.get("head_sha")


class DeploymentCommitsSyncHandler:
    def __init__(
        self,
        org_id: str,
        github_api: GithubApiService,
        code_repo_service: CodeRepoService,
        workflow_repo_service: WorkflowRepoService,
    ):
        self.org_id = org_id
        self._github = github_api
        self._code_repo_service = code_repo_service
        self._workflow_repo_service = workflow_repo_service

    def sync(self) -> int:
        """Fill in meta.shipped for runs that lack it. Returns runs updated."""
        org_repos: Dict[str, OrgRepo] = {
            str(repo.id): repo
            for repo in self._code_repo_service.get_active_org_repos(self.org_id)
            if repo.provider == CodeProvider.GITHUB.value
        }
        if not org_repos:
            return 0

        repo_workflows: List[RepoWorkflow] = (
            self._workflow_repo_service.get_active_repo_workflows_by_repo_ids_and_providers(
                list(org_repos.keys()), list(RepoWorkflowProviders)
            )
        )

        updated = 0
        for repo_workflow in repo_workflows:
            org_repo = org_repos[str(repo_workflow.org_repo_id)]
            try:
                updated += self._sync_repo_workflow(org_repo, repo_workflow)
            except GithubRateLimitExceeded:
                LOG.error("[Deployment Commits] GitHub rate limit hit, stopping")
                break
            except Exception as e:
                LOG.error(
                    f"[Deployment Commits] Error for workflow {repo_workflow.id} "
                    f"in repo {org_repo.name}: {str(e)}"
                )
        return updated

    def _sync_repo_workflow(
        self, org_repo: OrgRepo, repo_workflow: RepoWorkflow
    ) -> int:
        runs = (
            self._workflow_repo_service.get_successful_runs_with_meta_for_repo_workflow(
                str(repo_workflow.id)
            )
        )
        to_save: List[RepoWorkflowRuns] = []
        previous_revision_by_branch: Dict[str, str] = {}

        try:
            for run in runs:
                revision = get_run_revision(run)
                if not revision:
                    continue
                previous = previous_revision_by_branch.get(run.head_branch)
                previous_revision_by_branch[run.head_branch] = revision

                if isinstance((run.meta or {}).get("shipped"), dict):
                    continue

                shipped = self._get_shipped(org_repo, previous, revision)
                # Assign a new dict: in-place JSONB mutation is not tracked
                run.meta = {**(run.meta or {}), "shipped": shipped}
                to_save.append(run)
        finally:
            # Keep what was computed even if a later compare call failed
            if to_save:
                self._workflow_repo_service.save_repo_workflow_runs(to_save)

        return len(to_save)

    def _get_shipped(
        self, org_repo: OrgRepo, previous_revision: Optional[str], revision: str
    ) -> dict:
        if not previous_revision:
            return {
                "base": None,
                "status": NO_PREVIOUS_DEPLOY,
                "shas": None,
                "truncated": False,
            }
        if previous_revision == revision:
            return {
                "base": previous_revision,
                "status": "identical",
                "shas": [],
                "truncated": False,
            }
        result = self._github.compare_commits(
            org_repo.org_name, org_repo.name, previous_revision, revision
        )
        return {
            "base": previous_revision,
            "status": result.status,
            "shas": result.commit_shas,
            "truncated": result.truncated,
        }


def sync_org_deployment_commits(org_id: str):
    access_token = CoreRepoService().get_access_token(
        org_id, UserIdentityProvider.GITHUB
    )
    if not access_token:
        LOG.info(f"[Deployment Commits] No GitHub integration for org {org_id}")
        return
    handler = DeploymentCommitsSyncHandler(
        org_id,
        GithubApiService(access_token, get_custom_github_domain(org_id)),
        CodeRepoService(),
        WorkflowRepoService(),
    )
    updated = handler.sync()
    LOG.info(f"[Deployment Commits] Mapped commits for {updated} deployment(s)")


def get_commit_mapped_deployments(
    deployments: list, shipped_by_run_id: Dict[str, dict]
) -> Dict[object, set]:
    """Workflow deployments whose shipped commits are known -> their commit SHAs.

    Deployments missing from the result (PR-merge deployments, a workflow's
    first recorded deploy, runs not yet processed) use the time-based rule.
    """
    from mhq.service.deployments.models.models import DeploymentType

    mapped = {}
    for deployment in deployments:
        if deployment.deployment_type != DeploymentType.WORKFLOW:
            continue
        shipped = shipped_by_run_id.get(str(deployment.entity_id))
        if shipped and shipped.get("shas") is not None:
            mapped[deployment] = set(shipped["shas"])
    return mapped


def map_prs_to_commit_mapped_deployments(
    commit_mapped: Dict[object, set], prs: list
) -> Dict[object, list]:
    """Each deployment -> the PRs whose merge commit it shipped (same repo)."""
    prs_by_repo_sha = {(str(pr.repo_id), pr.merge_commit_sha): pr for pr in prs}
    result = {}
    for deployment, shas in commit_mapped.items():
        repo_id = str(deployment.repo_id)
        deployment_prs = [
            prs_by_repo_sha[(repo_id, sha)]
            for sha in shas
            if (repo_id, sha) in prs_by_repo_sha
        ]
        result[deployment] = sorted(deployment_prs, key=lambda pr: pr.state_changed_at)
    return result
