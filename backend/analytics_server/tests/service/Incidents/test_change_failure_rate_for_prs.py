"""
Change failure rate when incidents name their culprit PR.

A deployment failed if it shipped a culprit. Covers batching (several PRs per
deploy), several incidents on one deploy, the squad rule for shared deploys,
and the approval-gate case the old "last deploy before the incident" rule gets
wrong.
"""

from datetime import timedelta

from mhq.service.incidents.incidents import IncidentService
from mhq.store.models.code import PullRequestState
from mhq.store.models.incidents import IncidentType
from mhq.utils.time import Interval, time_now
from tests.factories.models import get_deployment, get_incident
from tests.factories.models.code import get_pull_request

T0 = time_now() - timedelta(days=10)


def _service():
    return IncidentService(None, None, None)


def _pr(number, author="ada"):
    return get_pull_request(
        number=str(number),
        author=author,
        state=PullRequestState.MERGED,
        state_changed_at=T0 + timedelta(hours=number),
    )


def _deploy(hours):
    return get_deployment(conducted_at=T0 + timedelta(hours=hours))


def _bug_on(pr, hours=0):
    return get_incident(
        id=str(pr.id),
        key=str(pr.id),
        incident_type=IncidentType.PRODUCTION_BUG,
        creation_date=T0 + timedelta(hours=hours),
        meta={"culprit_pr_id": str(pr.id)},
    )


def _revert_of(pr, hours=0):
    return get_incident(
        key=str(pr.id),
        incident_type=IncidentType.REVERT_PR,
        creation_date=pr.state_changed_at,
        meta={"original_pr": {"id": str(pr.id)}},
    )


def _cfr(deployments_with_prs, incidents, attributed=False):
    deployments = sorted(deployments_with_prs, key=lambda d: d.conducted_at)
    return _service().get_change_failure_rate_metrics_for_prs(
        deployments, deployments_with_prs, incidents, attributed
    )


def test_one_bad_pr_fails_the_whole_batched_deploy():
    a, b, c = _pr(1), _pr(2), _pr(3)
    d1, d2 = _deploy(10), _deploy(20)
    metrics = _cfr({d1: [a, b], d2: [c]}, [_bug_on(b, hours=12)])
    assert metrics.failed_deployments == {d1}
    assert metrics.total_deployments == {d1, d2}
    assert metrics.change_failure_rate == 50


def test_several_incidents_on_one_deploy_count_once():
    a, b = _pr(1), _pr(2)
    d1 = _deploy(10)
    metrics = _cfr({d1: [a, b]}, [_bug_on(a), _bug_on(b), _revert_of(a)])
    assert metrics.failed_deployments == {d1}
    assert metrics.change_failure_rate == 100


def test_approval_gated_deploy_is_blamed_not_the_previous_one():
    # d1 ships older work; culprit merges at hour 5; its deploy d2 is only
    # approved at hour 30. The old time rule would blame d1 (the last deploy
    # before the culprit merged).
    earlier, culprit = _pr(1), _pr(5)
    d1, d2 = _deploy(2), _deploy(30)
    revert = _revert_of(culprit)  # creation_date = culprit merge time
    metrics = _cfr({d1: [earlier], d2: [culprit]}, [revert])
    assert metrics.failed_deployments == {d2}

    old_rule = _service().get_change_failure_rate_metrics([d1, d2], [revert])
    assert old_rule.failed_deployments == {d1}  # the bug being fixed


def test_culprit_not_deployed_in_the_window_fails_nothing():
    a, pending = _pr(1), _pr(2)
    d1 = _deploy(10)
    metrics = _cfr({d1: [a]}, [_bug_on(pending)])
    assert metrics.failed_deployments == set()
    assert metrics.total_deployments == {d1}


def test_squad_view_counts_only_deploys_with_their_prs_and_their_culprits():
    # Shared deploy d1 carries ada's and grace's PRs; grace's breaks it.
    # d2 carries only grace's work; d3 carries nothing attributable.
    ada, grace_bad, grace_ok = _pr(1, "ada"), _pr(2, "grace"), _pr(3, "grace")
    d1, d2, d3 = _deploy(10), _deploy(20), _deploy(30)
    incidents = [_bug_on(grace_bad)]

    # deployments_with_prs arrives already filtered to the squad's PRs
    ada_view = _cfr({d1: [ada], d2: [], d3: []}, incidents, attributed=True)
    assert ada_view.total_deployments == {d1}
    assert ada_view.failed_deployments == set()

    grace_view = _cfr(
        {d1: [grace_bad], d2: [grace_ok], d3: []}, incidents, attributed=True
    )
    assert grace_view.total_deployments == {d1, d2}
    assert grace_view.failed_deployments == {d1}

    org_view = _cfr(
        {d1: [ada, grace_bad], d2: [grace_ok], d3: []}, incidents, attributed=False
    )
    assert org_view.total_deployments == {d1, d2, d3}
    assert org_view.failed_deployments == {d1}


def test_incident_without_a_culprit_keeps_the_time_rule():
    a = _pr(1)
    d1, d2 = _deploy(10), _deploy(20)
    service_incident = get_incident(
        key="pagerduty-1", creation_date=T0 + timedelta(hours=12), meta={}
    )
    metrics = _cfr({d1: [a], d2: []}, [service_incident])
    assert metrics.failed_deployments == {d1}


def test_drill_down_lists_each_deploys_incidents():
    a, b = _pr(1), _pr(2)
    d1, d2 = _deploy(10), _deploy(20)
    bug = _bug_on(b)
    mapping = _service().get_deployment_incidents_map_for_prs(
        [d1, d2], {d1: [a], d2: [b]}, [bug], False
    )
    assert mapping == {d1: [], d2: [bug]}


def test_weekly_trend_buckets_by_deploy_week():
    a, b = _pr(1), _pr(2)
    d1 = _deploy(0)
    d2 = get_deployment(conducted_at=T0 + timedelta(days=8))
    interval = Interval(T0 - timedelta(days=1), T0 + timedelta(days=9))
    weekly = _service().get_weekly_change_failure_rate_for_prs(
        interval, [d1, d2], {d1: [a], d2: [b]}, [_bug_on(b)], False
    )
    failed_weeks = [w for w, m in weekly.items() if m.failed_deployments]
    assert len(failed_weeks) == 1
    assert weekly[failed_weeks[0]].failed_deployments == {d2}
    assert sum(len(m.total_deployments) for m in weekly.values()) == 2


def test_merge_prefers_the_production_bug_over_a_revert_of_the_same_pr():
    culprit = _pr(1)
    revert, bug = _revert_of(culprit), _bug_on(culprit, hours=4)
    merged = IncidentService._merge_incidents([revert], [bug])
    assert merged == [bug]
