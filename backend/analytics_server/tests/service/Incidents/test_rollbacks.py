"""
Rollbacks as incidents: redeploying an older revision fails the deploys it
undid, with no bug or revert PR needed.
"""

from datetime import timedelta
from unittest.mock import MagicMock

from mhq.service.incidents.incidents import IncidentService
from mhq.service.incidents.rollbacks import (
    adapt_rollback_incident,
    get_incident_failed_deployment_ids,
)
from mhq.store.models.code import PullRequestState
from mhq.store.models.code.workflows.enums import RepoWorkflowProviders
from mhq.store.models.incidents import IncidentType
from mhq.utils.time import time_now
from tests.factories.models import get_deployment, get_incident
from tests.factories.models.code import get_pull_request, get_repo_workflow_run

T0 = time_now() - timedelta(days=10)


def _workflow():
    workflow = MagicMock(provider=RepoWorkflowProviders.CIRCLE_CI)
    workflow.name = "build-and-deploy/deploy-production"
    return workflow


def _rollback_run(rolled_back_deploys, hours=5):
    return get_repo_workflow_run(
        conducted_at=T0 + timedelta(hours=hours),
        event_actor="shervin",
        meta={
            "revision": "old",
            "shipped": {
                "base": "bad",
                "status": "behind",
                "shas": [],
                "rolled_back": [
                    {
                        "run_id": d.entity_id,
                        "conducted_at": d.conducted_at.isoformat(),
                        "revision": "bad",
                    }
                    for d in rolled_back_deploys
                ],
            },
        },
    )


def _deploy(hours):
    return get_deployment(conducted_at=T0 + timedelta(hours=hours))


def _pr(number, author="ada"):
    return get_pull_request(
        number=str(number), author=author, state=PullRequestState.MERGED
    )


def test_incident_runs_from_the_undone_deploy_to_the_rollback():
    d1, d2 = _deploy(1), _deploy(2)
    run = _rollback_run([d2, d1], hours=3)
    incident = adapt_rollback_incident(_workflow(), run, "mvp-api")
    assert incident.incident_type == IncidentType.ROLLBACK
    assert incident.title == "Rollback of mvp-api deploy"
    assert incident.creation_date == d1.conducted_at
    assert incident.resolved_date == run.conducted_at
    assert incident.assigned_to == "shervin"
    assert get_incident_failed_deployment_ids(incident) == [d2.entity_id, d1.entity_id]


def test_a_rollback_with_nothing_recorded_is_not_an_incident():
    run = _rollback_run([])
    assert adapt_rollback_incident(_workflow(), run) is None


def test_the_undone_deploy_fails_not_the_rollback_itself():
    good, bad = _deploy(1), _deploy(2)
    rollback = _deploy(3)
    incident = adapt_rollback_incident(_workflow(), _rollback_run([bad]))
    metrics = IncidentService(None, None, None).get_change_failure_rate_metrics_for_prs(
        [good, bad, rollback],
        {good: [_pr(1)], bad: [_pr(2)], rollback: []},
        [incident],
        False,
    )
    assert metrics.failed_deployments == {bad}
    assert metrics.total_deployments == {good, bad, rollback}


def test_a_rolled_back_shared_deploy_fails_for_every_squad_in_it():
    # A rollback does not say whose PR was at fault
    bad = _deploy(2)
    incident = adapt_rollback_incident(_workflow(), _rollback_run([bad]))
    service = IncidentService(None, None, None)
    for squad_prs in ([_pr(1, "ada")], [_pr(2, "grace")]):
        metrics = service.get_change_failure_rate_metrics_for_prs(
            [bad], {bad: squad_prs}, [incident], True
        )
        assert metrics.failed_deployments == {bad}


def test_a_rollback_and_a_regression_on_one_deploy_count_once():
    culprit = _pr(7)
    bad = _deploy(2)
    rollback = adapt_rollback_incident(_workflow(), _rollback_run([bad]))
    regression = get_incident(
        key=str(culprit.id),
        incident_type=IncidentType.REGRESSION,
        meta={"culprit_pr_id": str(culprit.id)},
    )
    mapping = IncidentService(None, None, None).get_deployment_incidents_map_for_prs(
        [bad], {bad: [culprit]}, [rollback, regression], False
    )
    assert len(mapping[bad]) == 2
    metrics = IncidentService(None, None, None).get_change_failure_rate_metrics_for_prs(
        [bad], {bad: [culprit]}, [rollback, regression], False
    )
    assert metrics.failed_deployments == {bad}
    assert metrics.change_failure_rate == 100
