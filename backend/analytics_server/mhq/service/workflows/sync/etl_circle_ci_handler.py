"""
CircleCI workflow ETL handler.

Middleware models a CI deployment as a workflow run. Zinc's CircleCI config has
one workflow, `build-and-deploy`, that runs on every push to every branch — the
deployment is a *job* inside it, behind a manual approval gate. So this handler
walks pipeline -> workflow -> job and records one RepoWorkflowRuns row per run
of the configured deploy job.

`RepoWorkflow.provider_workflow_id` holds "<workflow name>/<job name>", e.g.
"build-and-deploy/deploy-production". It is a free-text column, so this needs no
schema change, and using a compound value avoids any collision with the
(org_repo_id, provider_workflow_id) unique index shared with GitHub Actions.

`RepoWorkflow.meta` may carry {"project_slug": ..., "branch": ...}. Both fall
back to values derived from OrgRepo.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from mhq.exapi.circle_ci import CircleCIApiService
from mhq.exapi.models.circle_ci import (
    CircleCIDeployJobRun,
    CircleCIJob,
    CircleCIPipeline,
)
from mhq.service.workflows.sync.etl_provider_handler import WorkflowProviderETLHandler
from mhq.store.models import UserIdentityProvider
from mhq.store.models.code import (
    OrgRepo,
    RepoWorkflow,
    RepoWorkflowProviders,
    RepoWorkflowRuns,
    RepoWorkflowRunsStatus,
)
from mhq.store.repos.core import CoreRepoService
from mhq.store.repos.workflows import WorkflowRepoService
from mhq.utils.log import LOG
from mhq.utils.time import time_now

# The Integration row name a CircleCI token is stored under.
CIRCLE_CI_INTEGRATION_NAME = RepoWorkflowProviders.CIRCLE_CI.value  # "circle_ci"

# CircleCI job statuses that mean the job never actually ran. These are NOT
# failed deployments — an unapproved gate is "no deployment", and recording it
# as a failure would inflate the change failure rate denominator.
NON_RUN_STATUSES = {"not_run", "blocked", "unauthorized", "not_running"}

PENDING_STATUSES = {"running", "queued", "on_hold"}
SUCCESS_STATUSES = {"success"}
CANCELLED_STATUSES = {"canceled", "cancelled"}


class CircleCIETLHandler(WorkflowProviderETLHandler):
    def __init__(
        self,
        org_id: str,
        circle_ci_api_service: CircleCIApiService,
        workflow_repo_service: WorkflowRepoService,
    ):
        self.org_id = org_id
        self._api: CircleCIApiService = circle_ci_api_service
        self._workflow_repo_service = workflow_repo_service
        self._provider = RepoWorkflowProviders.CIRCLE_CI.value

    def check_pat_validity(self) -> bool:
        is_valid = self._api.check_token()
        if not is_valid:
            raise Exception("CircleCI personal API token is invalid")
        return is_valid

    def get_workflow_runs(
        self,
        org_repo: OrgRepo,
        repo_workflow: RepoWorkflow,
        bookmark: datetime,
    ) -> Tuple[List[RepoWorkflowRuns], datetime]:

        workflow_name, job_name = self._split_provider_workflow_id(
            repo_workflow.provider_workflow_id
        )
        if not job_name:
            raise Exception(
                f"[CircleCI Sync] RepoWorkflow {str(repo_workflow.id)} has "
                f"provider_workflow_id '{repo_workflow.provider_workflow_id}'. "
                "CircleCI workflows must be configured as "
                "'<workflow name>/<job name>', e.g. "
                "'build-and-deploy/deploy-production', because the deployment "
                "is a job rather than a whole workflow."
            )

        project_slug = self._get_project_slug(org_repo, repo_workflow)
        branch = self._get_branch(org_repo, repo_workflow)

        try:
            deploy_runs = self._collect_deploy_job_runs(
                project_slug, branch, workflow_name, job_name, bookmark
            )
        except Exception as e:
            raise Exception(
                f"[CircleCI Sync Repo Workflow Worker] Error fetching "
                f"{workflow_name}/{job_name} for {project_slug}: {str(e)}"
            )

        if not deploy_runs:
            LOG.info(
                f"[CircleCI Sync Repo Workflow Worker] No runs of "
                f"{workflow_name}/{job_name} found for {project_slug} "
                f"on branch {branch}. Org: {self.org_id}"
            )
            return [], bookmark

        new_bookmark = self._get_new_bookmark_time_stamp(deploy_runs)

        repo_workflow_runs = [
            self._adapt_deploy_run_to_workflow_run(str(repo_workflow.id), run)
            for run in deploy_runs
        ]

        return repo_workflow_runs, new_bookmark

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    def _collect_deploy_job_runs(
        self,
        project_slug: str,
        branch: str,
        workflow_name: str,
        job_name: str,
        bookmark: datetime,
    ) -> List[CircleCIDeployJobRun]:
        """Walk pipeline -> workflow -> job and keep the deploy job runs."""

        pipelines = self._api.get_pipelines(project_slug, branch, bookmark)
        runs: List[CircleCIDeployJobRun] = []

        for pipeline in pipelines:
            for raw_workflow in self._api.get_pipeline_workflows(pipeline.id):
                if raw_workflow.get("name") != workflow_name:
                    continue

                workflow_id = raw_workflow.get("id")
                if not workflow_id:
                    continue

                for job in self._api.get_workflow_jobs(workflow_id):
                    if job.name != job_name:
                        continue
                    if not self._is_real_run(job):
                        continue
                    runs.append(
                        CircleCIDeployJobRun(
                            pipeline=pipeline,
                            workflow_id=workflow_id,
                            workflow_name=workflow_name,
                            job=job,
                            project_slug=project_slug,
                        )
                    )

        return runs

    @staticmethod
    def _is_real_run(job: CircleCIJob) -> bool:
        """Did this job actually run?

        An approval gate nobody approved leaves the deploy job as `blocked` or
        `not_run` with no start time. That is the absence of a deployment, not
        a failed one.
        """
        if job.status in NON_RUN_STATUSES:
            return False
        return job.started_at is not None

    # ------------------------------------------------------------------
    # Adaptation
    # ------------------------------------------------------------------

    def _adapt_deploy_run_to_workflow_run(
        self, repo_workflow_id: str, run: CircleCIDeployJobRun
    ) -> RepoWorkflowRuns:
        existing = (
            self._workflow_repo_service.get_repo_workflow_run_by_provider_workflow_run_id(
                repo_workflow_id, str(run.job.id)
            )
        )
        workflow_run_id = existing.id if existing else uuid4()

        return RepoWorkflowRuns(
            id=workflow_run_id,
            repo_workflow_id=repo_workflow_id,
            provider_workflow_run_id=str(run.job.id),
            event_actor=run.pipeline.actor,
            head_branch=run.pipeline.branch,
            status=self._get_repo_workflow_status(run.job),
            created_at=time_now(),
            updated_at=time_now(),
            # The job's start, NOT the pipeline's creation. With a manual
            # approval gate those can be hours or days apart, and using the
            # pipeline time would skew both deployment timing and
            # merge-to-deploy.
            conducted_at=run.job.started_at,
            duration=self._get_duration(run.job),
            meta=self._build_meta(run),
            html_url=run.html_url,
        )

    @staticmethod
    def _get_repo_workflow_status(job: CircleCIJob) -> RepoWorkflowRunsStatus:
        if job.status in SUCCESS_STATUSES:
            return RepoWorkflowRunsStatus.SUCCESS
        if job.status in CANCELLED_STATUSES:
            # The GitHub Actions handler folds cancellations into FAILURE and
            # never emits CANCELLED. CircleCI reports it distinctly, so record
            # it properly.
            return RepoWorkflowRunsStatus.CANCELLED
        if job.status in PENDING_STATUSES:
            return RepoWorkflowRunsStatus.PENDING
        return RepoWorkflowRunsStatus.FAILURE

    @staticmethod
    def _get_duration(job: CircleCIJob) -> Optional[int]:
        if not (job.started_at and job.stopped_at):
            return None
        return int((job.stopped_at - job.started_at).total_seconds())

    @staticmethod
    def _build_meta(run: CircleCIDeployJobRun) -> Dict:
        return {
            "provider": RepoWorkflowProviders.CIRCLE_CI.value,
            "project_slug": run.project_slug,
            "pipeline_id": run.pipeline.id,
            "pipeline_number": run.pipeline.number,
            "revision": run.pipeline.revision,
            "workflow_id": run.workflow_id,
            "workflow_name": run.workflow_name,
            "job_name": run.job.name,
            "job_number": run.job.job_number,
            "job_status": run.job.status,
        }

    def _get_new_bookmark_time_stamp(
        self, deploy_runs: List[CircleCIDeployJobRun]
    ) -> datetime:
        """Rewind to the earliest still-running deploy so it is re-read.

        Mirrors the GitHub Actions handler: a job that has not finished has no
        final status or duration yet, so the bookmark must not move past it.
        """
        pending = [
            run.job.started_at
            for run in deploy_runs
            if run.job.status in PENDING_STATUSES and run.job.started_at
        ]
        return min(pending) if pending else time_now()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _split_provider_workflow_id(
        provider_workflow_id: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        if not provider_workflow_id or "/" not in provider_workflow_id:
            return provider_workflow_id, None
        workflow_name, _, job_name = provider_workflow_id.partition("/")
        return workflow_name.strip() or None, job_name.strip() or None

    @staticmethod
    def _get_project_slug(org_repo: OrgRepo, repo_workflow: RepoWorkflow) -> str:
        meta = repo_workflow.meta or {}
        if meta.get("project_slug"):
            return meta["project_slug"]
        # Classic GitHub OAuth projects are gh/<org>/<repo>.
        return f"gh/{org_repo.org_name}/{org_repo.name}"

    @staticmethod
    def _get_branch(org_repo: OrgRepo, repo_workflow: RepoWorkflow) -> str:
        meta = repo_workflow.meta or {}
        return meta.get("branch") or org_repo.default_branch or "main"


def get_circle_ci_etl_handler(org_id: str) -> CircleCIETLHandler:
    def _get_access_token() -> Optional[str]:
        # get_access_token queries Integration.name == provider.value, so this
        # works once UserIdentityProvider.CIRCLECI ("circle_ci") exists. Same
        # decryption path as the GitHub token — nothing bespoke.
        core_repo_service = CoreRepoService()
        access_token = core_repo_service.get_access_token(
            org_id, UserIdentityProvider.CIRCLECI
        )
        if not access_token:
            LOG.error(
                f"No CircleCI token found for org {org_id} under integration "
                f"'{CIRCLE_CI_INTEGRATION_NAME}'. Run setup_circleci.py first."
            )
        return access_token

    return CircleCIETLHandler(
        org_id,
        CircleCIApiService(_get_access_token()),
        WorkflowRepoService(),
    )
