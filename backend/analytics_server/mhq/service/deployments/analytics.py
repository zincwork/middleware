from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from mhq.utils.dict import (
    get_average_of_dict_values,
    get_key_to_count_map_from_key_to_list_map,
)

from .deployment_service import DeploymentsService, get_deployments_service
from mhq.store.models.code.filter import PRFilter
from mhq.store.models.code.pull_requests import PullRequest
from mhq.store.models.code.repository import TeamRepos
from mhq.store.models.code.workflows.filter import WorkflowFilter
from mhq.service.deployments.deployment_commits import (
    get_commit_mapped_deployments,
    map_prs_to_commit_mapped_deployments,
)
from mhq.service.deployments.models.models import (
    Deployment,
    DeploymentFrequencyMetrics,
    DeploymentStatus,
    DeploymentType,
)

from mhq.store.repos.code import CodeRepoService
from mhq.store.repos.workflows import WorkflowRepoService
from mhq.utils.time import Interval, generate_expanded_buckets


class DeploymentAnalyticsService:
    def __init__(
        self,
        deployments_service: DeploymentsService,
        code_repo_service: CodeRepoService,
        workflow_repo_service: Optional[WorkflowRepoService] = None,
    ):
        self.deployments_service = deployments_service
        self.code_repo_service = code_repo_service
        # Optional so unit tests can omit it; without it every deployment
        # falls back to the time-based PR mapping.
        self.workflow_repo_service = workflow_repo_service

    def get_team_all_deployments_in_interval_with_related_prs(
        self,
        team_id: str,
        interval: Interval,
        pr_filter: PRFilter,
        workflow_filter: WorkflowFilter,
    ) -> Dict[str, Dict[Deployment, List[PullRequest]]]:
        """
        Retrieves all deployments within the specified interval for a given team,
        along with related pull requests. Returns A dictionary mapping repository IDs to lists of deployments along with
        related pull requests. Each deployment is associated with a list of pull requests that contributed to it.
        """

        deployments: List[Deployment] = (
            self.deployments_service.get_team_all_deployments_in_interval(
                team_id, interval, pr_filter, workflow_filter
            )
        )

        team_repos: List[TeamRepos] = self._get_team_repos_by_team_id(team_id)
        repo_ids: List[str] = [str(team_repo.org_repo_id) for team_repo in team_repos]

        repo_id_to_deployments_with_pr_map: Dict[
            str, Dict[Deployment, List[PullRequest]]
        ] = defaultdict(dict)

        # Deployments whose shipped commits are known are mapped exactly, by
        # merge commit (see deployment_commits.py). PRs are loaded by SHA, not
        # by merge date, because a deploy early in the interval can ship PRs
        # merged before it started.
        commit_mapped = self._get_commit_mapped_deployments(deployments)
        assigned_pr_ids = set()
        if commit_mapped:
            shas = list(set().union(*commit_mapped.values()))
            commit_prs = self.code_repo_service.get_merged_prs_by_merge_commit_shas(
                repo_ids, shas, pr_filter
            )
            for deployment, prs in map_prs_to_commit_mapped_deployments(
                commit_mapped, commit_prs
            ).items():
                repo_id_to_deployments_with_pr_map[str(deployment.repo_id)][
                    deployment
                ] = prs
                assigned_pr_ids.update(str(pr.id) for pr in prs)
            deployments = [d for d in deployments if d not in commit_mapped]

        pull_requests: List[PullRequest] = [
            pr
            for pr in self.code_repo_service.get_prs_merged_in_interval(
                repo_ids, interval, pr_filter
            )
            if str(pr.id) not in assigned_pr_ids
        ]

        repo_id_branch_to_pr_list_map: Dict[Tuple[str, str], List[PullRequest]] = (
            self._map_prs_to_repo_id_and_base_branch(pull_requests)
        )
        repo_id_branch_to_deployments_map: Dict[Tuple[str, str], List[Deployment]] = (
            self._map_deployments_to_repo_id_and_head_branch(deployments)
        )

        for (
            repo_id,
            base_branch,
        ), deployments in repo_id_branch_to_deployments_map.items():
            relevant_prs: List[PullRequest] = repo_id_branch_to_pr_list_map.get(
                (repo_id, base_branch), []
            )
            deployments_pr_map: Dict[Deployment, List[PullRequest]] = (
                self._map_prs_to_deployments(relevant_prs, deployments)
            )

            repo_id_to_deployments_with_pr_map[repo_id].update(deployments_pr_map)

        return repo_id_to_deployments_with_pr_map

    def get_team_deployment_frequency_metrics(
        self,
        team_id: str,
        interval: Interval,
        pr_filter: PRFilter,
        workflow_filter: WorkflowFilter,
    ) -> DeploymentFrequencyMetrics:

        team_successful_deployments = self._get_successful_deployments_for_metrics(
            team_id, interval, pr_filter, workflow_filter
        )

        return self._get_deployment_frequency_metrics(
            team_successful_deployments, interval
        )

    def get_weekly_deployment_frequency_trends(
        self,
        team_id: str,
        interval: Interval,
        pr_filter: PRFilter,
        workflow_filter: WorkflowFilter,
    ) -> Dict[datetime, int]:

        # Must use the same attribution as the headline figure — the card shows
        # both together and they have to agree.
        team_successful_deployments = self._get_successful_deployments_for_metrics(
            team_id, interval, pr_filter, workflow_filter
        )

        team_weekly_deployments = generate_expanded_buckets(
            team_successful_deployments, interval, "conducted_at", "weekly"
        )

        return get_key_to_count_map_from_key_to_list_map(team_weekly_deployments)

    def _get_successful_deployments_for_metrics(
        self,
        team_id: str,
        interval: Interval,
        pr_filter: PRFilter,
        workflow_filter: WorkflowFilter,
    ) -> List[Deployment]:
        """Successful deployments, attributed to the team when a filter is set.

        Workflow deployments (CircleCI, GitHub Actions) are filtered on branch
        only, so a PR-level filter such as an author or head-branch list would
        otherwise have no effect on this metric — leaving two filtered DORA
        cards beside two unfiltered ones, all looking equally authoritative.

        When such a filter is active, count a deployment only if it carried at
        least one PR that survived the filter: "how often did this team's work
        reach production".

        Two consequences, both deliberate:
          * a deploy carrying nothing attributable — a re-run, a config-only
            change — counts for nobody, so per-team figures sum to LESS than
            the org-wide figure;
          * a deploy carrying three teams' PRs counts once for each, so they
            can also sum to MORE. Which way it goes depends on the mix.
        """
        if not self._is_pr_attributed(pr_filter):
            return self.deployments_service.get_team_successful_deployments_in_interval(
                team_id, interval, pr_filter, workflow_filter
            )

        repo_id_to_deployments_with_prs = (
            self.get_team_all_deployments_in_interval_with_related_prs(
                team_id, interval, pr_filter, workflow_filter
            )
        )

        return [
            deployment
            for deployments_with_prs in repo_id_to_deployments_with_prs.values()
            for deployment, prs in deployments_with_prs.items()
            if prs and deployment.status == DeploymentStatus.SUCCESS
        ]

    def _get_commit_mapped_deployments(
        self, deployments: List[Deployment]
    ) -> Dict[Deployment, set]:
        if not self.workflow_repo_service:
            return {}
        run_ids = [
            str(d.entity_id)
            for d in deployments
            if d.deployment_type == DeploymentType.WORKFLOW
        ]
        shipped_by_run_id = self.workflow_repo_service.get_shipped_commits_by_run_ids(
            run_ids
        )
        return get_commit_mapped_deployments(deployments, shipped_by_run_id)

    @staticmethod
    def is_pr_attributed(pr_filter: PRFilter) -> bool:
        return DeploymentAnalyticsService._is_pr_attributed(pr_filter)

    @staticmethod
    def _is_pr_attributed(pr_filter: PRFilter) -> bool:
        """Is a team-level PR filter active?

        Deliberately ignores base_branches and repo_filters: production-branch
        filtering is always on, and must not push every request down the more
        expensive attributed path.
        """
        if not pr_filter:
            return False
        return bool(pr_filter.authors) or bool(pr_filter.head_branches)

    def _map_prs_to_repo_id_and_base_branch(
        self, pull_requests: List[PullRequest]
    ) -> Dict[Tuple[str, str], List[PullRequest]]:
        repo_id_branch_pr_map: Dict[Tuple[str, str], List[PullRequest]] = defaultdict(
            list
        )
        for pr in pull_requests:
            repo_id = str(pr.repo_id)
            base_branch = pr.base_branch
            repo_id_branch_pr_map[(repo_id, base_branch)].append(pr)
        return repo_id_branch_pr_map

    def _map_deployments_to_repo_id_and_head_branch(
        self, deployments: List[Deployment]
    ) -> Dict[Tuple[str, str], List[Deployment]]:
        repo_id_branch_deployments_map: Dict[Tuple[str, str], List[Deployment]] = (
            defaultdict(list)
        )
        for deployment in deployments:
            repo_id = str(deployment.repo_id)
            head_branch = deployment.head_branch
            repo_id_branch_deployments_map[(repo_id, head_branch)].append(deployment)
        return repo_id_branch_deployments_map

    def _map_prs_to_deployments(
        self, pull_requests: List[PullRequest], deployments: List[Deployment]
    ) -> Dict[Deployment, List[PullRequest]]:
        """
        Maps the pull requests to the deployments they were included in.
        This method takes a sorted list of pull requests and a sorted list of deployments and returns a dictionary
        """
        pr_count = 0
        deployment_count = 0
        deployment_pr_map = defaultdict(
            list, {deployment: [] for deployment in deployments}
        )

        while pr_count < len(pull_requests) and deployment_count < len(deployments):
            pr = pull_requests[pr_count]
            deployment = deployments[deployment_count]

            # Check if the PR was merged before or at the same time as the deployment
            if pr.state_changed_at <= deployment.conducted_at:
                deployment_pr_map[deployment].append(pr)
                pr_count += 1
            else:
                deployment_count += 1

        return deployment_pr_map

    def _get_team_repos_by_team_id(self, team_id: str) -> List[TeamRepos]:
        return self.code_repo_service.get_active_team_repos_by_team_id(team_id)

    def _get_deployment_frequency_from_date_to_deployment_map(
        self, date_to_deployment_map: Dict[datetime, List[Deployment]]
    ) -> int:
        """
        This method takes a dict of datetime representing (day/week/month) to Deployments and returns avg deployment frequency
        """

        date_to_deployment_count_map: Dict[datetime, int] = (
            get_key_to_count_map_from_key_to_list_map(date_to_deployment_map)
        )

        return get_average_of_dict_values(date_to_deployment_count_map)

    def _get_deployment_frequency_metrics(
        self, successful_deployments: List[Deployment], interval: Interval
    ) -> DeploymentFrequencyMetrics:

        successful_deployments = list(
            filter(
                lambda x: interval.from_time <= x.conducted_at <= interval.to_time,
                successful_deployments,
            )
        )

        team_daily_deployments = generate_expanded_buckets(
            successful_deployments, interval, "conducted_at", "daily"
        )
        team_weekly_deployments = generate_expanded_buckets(
            successful_deployments, interval, "conducted_at", "weekly"
        )
        team_monthly_deployments = generate_expanded_buckets(
            successful_deployments, interval, "conducted_at", "monthly"
        )

        daily_deployment_frequency = (
            self._get_deployment_frequency_from_date_to_deployment_map(
                team_daily_deployments
            )
        )

        weekly_deployment_frequency = (
            self._get_deployment_frequency_from_date_to_deployment_map(
                team_weekly_deployments
            )
        )

        monthly_deployment_frequency = (
            self._get_deployment_frequency_from_date_to_deployment_map(
                team_monthly_deployments
            )
        )

        weekly_deployment_frequency = self._adjust_frequency_for_granularity(
            weekly_deployment_frequency, daily_deployment_frequency, 7
        )
        monthly_deployment_frequency = self._adjust_frequency_for_granularity(
            monthly_deployment_frequency, daily_deployment_frequency, 30
        )

        return DeploymentFrequencyMetrics(
            len(successful_deployments),
            daily_deployment_frequency,
            weekly_deployment_frequency,
            monthly_deployment_frequency,
        )

    def _get_weekly_deployment_frequency_trends(
        self, successful_deployments: List[Deployment], interval: Interval
    ) -> Dict[datetime, int]:

        successful_deployments = list(
            filter(
                lambda x: interval.from_time <= x.conducted_at <= interval.to_time,
                successful_deployments,
            )
        )

        team_weekly_deployments = generate_expanded_buckets(
            successful_deployments, interval, "conducted_at", "weekly"
        )

        return get_key_to_count_map_from_key_to_list_map(team_weekly_deployments)

    def _adjust_frequency_for_granularity(
        self, frequency: int, daily_frequency: int, days_in_granularity: int
    ) -> int:
        if frequency < daily_frequency * days_in_granularity:
            frequency = daily_frequency * days_in_granularity
        return frequency


def get_deployment_analytics_service() -> DeploymentAnalyticsService:
    return DeploymentAnalyticsService(
        get_deployments_service(), CodeRepoService(), WorkflowRepoService()
    )
