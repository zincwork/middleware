"""
Tests for PR-attributed deployment frequency.

Workflow deployments (CircleCI, GitHub Actions) are filtered on branch only, so
without this a team filter has no effect on deployment frequency — you would
get two filtered DORA cards sitting next to two unfiltered ones, all looking
equally authoritative.

The rule: when a PR-level filter is active, a deployment counts for the team
only if it carried at least one PR that survived the filter.
"""

from unittest.mock import MagicMock

from mhq.service.deployments.analytics import DeploymentAnalyticsService
from mhq.service.deployments.models.models import DeploymentStatus
from mhq.store.models.code.filter import PRFilter


def _deployment(name, status=DeploymentStatus.SUCCESS):
    deployment = MagicMock()
    deployment.status = status
    deployment.name = name
    # Deployments are used as dict keys by the PR mapper, so they must hash.
    deployment.__hash__ = lambda self: hash(name)
    return deployment


def _service(deployments_with_prs=None, plain_deployments=None):
    deployments_service = MagicMock()
    deployments_service.get_team_successful_deployments_in_interval.return_value = (
        plain_deployments or []
    )
    service = DeploymentAnalyticsService(deployments_service, MagicMock())
    service.get_team_all_deployments_in_interval_with_related_prs = MagicMock(
        return_value=deployments_with_prs or {}
    )
    return service


class TestIsPrAttributed:
    def test_no_filter_is_not_attributed(self):
        assert DeploymentAnalyticsService._is_pr_attributed(None) is False
        assert DeploymentAnalyticsService._is_pr_attributed(PRFilter()) is False

    def test_branch_or_repo_filters_alone_do_not_trigger_it(self):
        # Production-branch filtering is always on. It must not switch the
        # metric onto the expensive PR-attributed path for every request.
        pr_filter = PRFilter(base_branches=["^main$"], repo_filters={"r": {}})
        assert DeploymentAnalyticsService._is_pr_attributed(pr_filter) is False

    def test_authors_trigger_it(self):
        assert (
            DeploymentAnalyticsService._is_pr_attributed(PRFilter(authors=["ada"]))
            is True
        )

    def test_head_branches_trigger_it(self):
        assert (
            DeploymentAnalyticsService._is_pr_attributed(
                PRFilter(head_branches=["^blue/"])
            )
            is True
        )


class TestUnfilteredPath:
    def test_falls_through_to_the_plain_count(self):
        plain = [_deployment("d1"), _deployment("d2")]
        service = _service(plain_deployments=plain)

        result = service._get_successful_deployments_for_metrics(
            "team-1", MagicMock(), PRFilter(base_branches=["^main$"]), MagicMock()
        )

        assert result == plain
        service.get_team_all_deployments_in_interval_with_related_prs.assert_not_called()


class TestAttributedPath:
    def test_keeps_only_deployments_carrying_a_matching_pr(self):
        carried = _deployment("carried")
        empty = _deployment("empty")
        service = _service(
            deployments_with_prs={"repo-1": {carried: ["pr-1"], empty: []}}
        )

        result = service._get_successful_deployments_for_metrics(
            "team-1", MagicMock(), PRFilter(authors=["ada"]), MagicMock()
        )

        assert result == [carried]

    def test_excludes_unsuccessful_deployments(self):
        # The with-related-prs path returns all deployments, not just
        # successful ones, so the status check has to happen here.
        failed = _deployment("failed", status=DeploymentStatus.FAILURE)
        ok = _deployment("ok")
        service = _service(
            deployments_with_prs={"repo-1": {failed: ["pr-1"], ok: ["pr-2"]}}
        )

        result = service._get_successful_deployments_for_metrics(
            "team-1", MagicMock(), PRFilter(authors=["ada"]), MagicMock()
        )

        assert result == [ok]

    def test_spans_multiple_repos(self):
        a = _deployment("a")
        b = _deployment("b")
        service = _service(
            deployments_with_prs={"repo-1": {a: ["pr-1"]}, "repo-2": {b: ["pr-2"]}}
        )

        result = service._get_successful_deployments_for_metrics(
            "team-1", MagicMock(), PRFilter(head_branches=["^blue/"]), MagicMock()
        )

        assert set(d.name for d in result) == {"a", "b"}

    def test_a_deploy_carrying_nothing_attributable_counts_for_nobody(self):
        # Documented consequence: re-runs and config-only deploys drop out, so
        # per-team figures sum to LESS than the org-wide figure.
        service = _service(
            deployments_with_prs={"repo-1": {_deployment("rerun"): []}},
            plain_deployments=[_deployment("rerun")],
        )

        attributed = service._get_successful_deployments_for_metrics(
            "team-1", MagicMock(), PRFilter(authors=["ada"]), MagicMock()
        )
        unfiltered = service._get_successful_deployments_for_metrics(
            "team-1", MagicMock(), PRFilter(), MagicMock()
        )

        assert len(attributed) == 0
        assert len(unfiltered) == 1
