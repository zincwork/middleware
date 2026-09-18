"""
Tests for the CircleCI workflow ETL handler.

The behaviour that matters and is easy to get wrong:

  * the deploy signal is a JOB inside a workflow, not the workflow itself
  * `conducted_at` must be the job's start, not the pipeline's creation, because
    an approval gate can sit between them for days
  * a deploy job that never ran (unapproved gate) is the ABSENCE of a
    deployment, not a failed one
  * an existing row must be updated in place, not duplicated
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytz

from mhq.exapi.circle_ci import parse_circle_ci_datetime
from mhq.exapi.models.circle_ci import (
    CircleCIDeployJobRun,
    CircleCIJob,
    CircleCIPipeline,
)
from mhq.service.workflows.sync.etl_circle_ci_handler import CircleCIETLHandler
from mhq.store.models.code import RepoWorkflowRunsStatus

ORG_ID = "9c8f2a24-1c1f-4b7f-9a2e-4a26d1c1f0aa"
REPO_WORKFLOW_ID = "0f3a2b1c-1111-2222-3333-444455556666"
PROJECT_SLUG = "gh/zincwork/mvp-api"


def _handler(existing_run=None):
    workflow_repo_service = MagicMock()
    workflow_repo_service.get_repo_workflow_run_by_provider_workflow_run_id.return_value = (
        existing_run
    )
    return CircleCIETLHandler(ORG_ID, MagicMock(), workflow_repo_service)


def _pipeline(created="2026-09-01T09:00:00Z", branch="main", actor="ada"):
    return CircleCIPipeline(
        id="pipe-1",
        number=412,
        branch=branch,
        revision="abc123",
        actor=actor,
        created_at=parse_circle_ci_datetime(created),
        raw={},
    )


def _job(
    status="success",
    started="2026-09-01T15:30:00Z",
    stopped="2026-09-01T15:34:20Z",
    name="deploy-production",
    job_number=88,
):
    return CircleCIJob(
        id="job-uuid-1",
        name=name,
        status=status,
        job_number=job_number,
        started_at=parse_circle_ci_datetime(started),
        stopped_at=parse_circle_ci_datetime(stopped),
        raw={},
    )


def _run(pipeline=None, job=None):
    return CircleCIDeployJobRun(
        pipeline=pipeline or _pipeline(),
        workflow_id="wf-uuid-1",
        workflow_name="build-and-deploy",
        job=job or _job(),
        project_slug=PROJECT_SLUG,
    )


class TestTimestampParsing:
    def test_parses_fractional_seconds(self):
        # CircleCI sends milliseconds where GitHub does not, so Middleware's
        # ISO_8601_DATE_FORMAT cannot be reused.
        parsed = parse_circle_ci_datetime("2026-09-04T10:15:32.123Z")
        assert parsed == datetime(2026, 9, 4, 10, 15, 32, 123000, tzinfo=pytz.UTC)

    def test_parses_whole_seconds(self):
        parsed = parse_circle_ci_datetime("2026-09-04T10:15:32Z")
        assert parsed == datetime(2026, 9, 4, 10, 15, 32, tzinfo=pytz.UTC)

    def test_none_and_garbage_are_tolerated(self):
        assert parse_circle_ci_datetime(None) is None
        assert parse_circle_ci_datetime("") is None
        assert parse_circle_ci_datetime("not a date") is None


class TestProviderWorkflowIdParsing:
    def test_splits_workflow_and_job(self):
        assert CircleCIETLHandler._split_provider_workflow_id(
            "build-and-deploy/deploy-production"
        ) == ("build-and-deploy", "deploy-production")

    def test_tolerates_whitespace(self):
        assert CircleCIETLHandler._split_provider_workflow_id(
            " build-and-deploy / deploy-master "
        ) == ("build-and-deploy", "deploy-master")

    def test_workflow_only_yields_no_job(self):
        # Must be detectable so get_workflow_runs can raise a useful error
        # rather than silently syncing whole-workflow runs as deployments.
        assert CircleCIETLHandler._split_provider_workflow_id(
            "build-and-deploy"
        ) == ("build-and-deploy", None)


class TestIsRealRun:
    def test_success_is_a_run(self):
        assert CircleCIETLHandler._is_real_run(_job(status="success")) is True

    def test_failed_is_a_run(self):
        # A deploy that was attempted and failed IS a deployment for change
        # failure rate purposes.
        assert CircleCIETLHandler._is_real_run(_job(status="failed")) is True

    def test_unapproved_gate_is_not_a_run(self):
        for status in ("blocked", "not_run", "unauthorized", "not_running"):
            job = _job(status=status, started=None, stopped=None)
            assert CircleCIETLHandler._is_real_run(job) is False, status

    def test_no_start_time_is_not_a_run(self):
        assert (
            CircleCIETLHandler._is_real_run(_job(status="running", started=None))
            is False
        )


class TestStatusMapping:
    def test_success(self):
        assert (
            CircleCIETLHandler._get_repo_workflow_status(_job(status="success"))
            == RepoWorkflowRunsStatus.SUCCESS
        )

    def test_cancelled_is_not_folded_into_failure(self):
        # The GitHub Actions handler never emits CANCELLED. CircleCI reports it
        # distinctly, so record it properly.
        for status in ("canceled", "cancelled"):
            assert (
                CircleCIETLHandler._get_repo_workflow_status(_job(status=status))
                == RepoWorkflowRunsStatus.CANCELLED
            ), status

    def test_pending(self):
        for status in ("running", "queued", "on_hold"):
            assert (
                CircleCIETLHandler._get_repo_workflow_status(_job(status=status))
                == RepoWorkflowRunsStatus.PENDING
            ), status

    def test_everything_else_is_failure(self):
        for status in ("failed", "infrastructure_fail", "timedout"):
            assert (
                CircleCIETLHandler._get_repo_workflow_status(_job(status=status))
                == RepoWorkflowRunsStatus.FAILURE
            ), status


class TestDuration:
    def test_duration_in_seconds(self):
        assert CircleCIETLHandler._get_duration(_job()) == 260

    def test_missing_stop_time_gives_none(self):
        assert CircleCIETLHandler._get_duration(_job(stopped=None)) is None

    def test_missing_start_time_gives_none(self):
        assert CircleCIETLHandler._get_duration(_job(started=None)) is None


class TestAdaptation:
    def test_maps_every_field(self):
        run = _run()
        adapted = _handler()._adapt_deploy_run_to_workflow_run(REPO_WORKFLOW_ID, run)

        assert str(adapted.repo_workflow_id) == REPO_WORKFLOW_ID
        assert adapted.provider_workflow_run_id == "job-uuid-1"
        assert adapted.event_actor == "ada"
        assert adapted.head_branch == "main"
        assert adapted.status == RepoWorkflowRunsStatus.SUCCESS
        assert adapted.duration == 260
        assert adapted.html_url == (
            "https://app.circleci.com/pipelines/gh/zincwork/mvp-api"
            "/412/workflows/wf-uuid-1/jobs/88"
        )
        assert adapted.meta["job_name"] == "deploy-production"
        assert adapted.meta["pipeline_number"] == 412
        assert adapted.meta["revision"] == "abc123"

    def test_conducted_at_is_the_job_start_not_the_pipeline_creation(self):
        # This is the one that matters. The pipeline was created at 09:00 and
        # the deploy was approved and ran at 15:30 — six and a half hours
        # apart. Using the pipeline time would poison merge-to-deploy.
        run = _run(
            pipeline=_pipeline(created="2026-09-01T09:00:00Z"),
            job=_job(started="2026-09-01T15:30:00Z"),
        )
        adapted = _handler()._adapt_deploy_run_to_workflow_run(REPO_WORKFLOW_ID, run)

        assert adapted.conducted_at == parse_circle_ci_datetime(
            "2026-09-01T15:30:00Z"
        )
        assert adapted.conducted_at != run.pipeline.created_at
        assert adapted.conducted_at - run.pipeline.created_at == timedelta(
            hours=6, minutes=30
        )

    def test_existing_run_is_updated_not_duplicated(self):
        existing = MagicMock()
        existing.id = "existing-uuid"
        adapted = _handler(existing_run=existing)._adapt_deploy_run_to_workflow_run(
            REPO_WORKFLOW_ID, _run()
        )
        assert adapted.id == "existing-uuid"

    def test_html_url_falls_back_without_a_job_number(self):
        run = _run(job=_job(job_number=None))
        assert run.html_url == (
            "https://app.circleci.com/pipelines/gh/zincwork/mvp-api"
            "/412/workflows/wf-uuid-1"
        )


class TestBookmark:
    def test_rewinds_to_the_earliest_pending_deploy(self):
        pending_early = _run(job=_job(status="running", started="2026-09-01T10:00:00Z"))
        pending_late = _run(job=_job(status="running", started="2026-09-01T18:00:00Z"))
        finished = _run(job=_job(status="success"))

        bookmark = _handler()._get_new_bookmark_time_stamp(
            [finished, pending_late, pending_early]
        )
        assert bookmark == parse_circle_ci_datetime("2026-09-01T10:00:00Z")

    def test_moves_to_now_when_nothing_is_pending(self):
        before = datetime.now(pytz.UTC)
        bookmark = _handler()._get_new_bookmark_time_stamp([_run()])
        assert bookmark >= before


def _org_repo(org_name="zincwork", repo_name="mvp-api", default_branch="main"):
    org_repo = MagicMock(org_name=org_name, default_branch=default_branch)
    # `name` is reserved by the MagicMock constructor — it names the mock
    # rather than setting an attribute — so it has to be assigned afterwards.
    org_repo.name = repo_name
    return org_repo


class TestConfiguration:
    def test_project_slug_is_derived_from_the_repo(self):
        repo_workflow = MagicMock(meta={})
        assert (
            CircleCIETLHandler._get_project_slug(_org_repo(), repo_workflow)
            == "gh/zincwork/mvp-api"
        )

    def test_explicit_project_slug_wins(self):
        org_repo = _org_repo()
        repo_workflow = MagicMock(meta={"project_slug": "circleci/abc/def"})
        assert (
            CircleCIETLHandler._get_project_slug(org_repo, repo_workflow)
            == "circleci/abc/def"
        )

    def test_branch_falls_back_to_the_repo_default(self):
        assert (
            CircleCIETLHandler._get_branch(_org_repo(), MagicMock(meta={})) == "main"
        )

    def test_explicit_branch_wins(self):
        repo_workflow = MagicMock(meta={"branch": "release"})
        assert (
            CircleCIETLHandler._get_branch(_org_repo(), repo_workflow) == "release"
        )
