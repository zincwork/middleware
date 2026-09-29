"""
Tests for mapping deployments to the PRs they shipped, by commit.

The case that motivates it: approval-gated deploys. PR A merges and its
pipeline waits; PR B merges and its pipeline waits; someone approves A's
deploy. By time, that deploy "shipped" B too. By commit, it shipped only A.
"""

from datetime import timedelta
from unittest.mock import MagicMock

from mhq.exapi.models.github import GithubCompareResult
from mhq.service.deployments.deployment_commits import (
    NO_PREVIOUS_DEPLOY,
    DeploymentCommitsSyncHandler,
    get_commit_mapped_deployments,
    map_prs_to_commit_mapped_deployments,
)
from mhq.service.deployments.models.models import DeploymentType
from mhq.store.models.code import PullRequestState
from mhq.utils.time import time_now
from tests.factories.models.code import (
    get_deployment,
    get_pull_request,
    get_repo_workflow_run,
)

T0 = time_now() - timedelta(days=3)
REPO_ID = "3f1c2a60-7d8e-4b8a-9c11-5a2f0a1b2c3d"


def _run(revision, hours, branch="main", shipped=None):
    meta = {"revision": revision} if revision else {}
    if shipped is not None:
        meta["shipped"] = shipped
    return get_repo_workflow_run(
        head_branch=branch, conducted_at=T0 + timedelta(hours=hours), meta=meta
    )


def _handler(runs, compare=None):
    org_repo = MagicMock(id=REPO_ID, org_name="zincwork", provider="github")
    org_repo.name = "mvp-api"
    code_repo_service = MagicMock()
    code_repo_service.get_active_org_repos.return_value = [org_repo]
    workflow_repo_service = MagicMock()
    workflow_repo_service.get_active_repo_workflows_by_repo_ids_and_providers.return_value = [
        MagicMock(id="wf-1", org_repo_id=REPO_ID)
    ]
    workflow_repo_service.get_successful_runs_with_meta_for_repo_workflow.return_value = (
        runs
    )
    github = MagicMock()
    github.compare_commits.side_effect = compare or (
        lambda org, repo, base, head: GithubCompareResult(
            status="ahead", commit_shas=[f"{base}..{head}"], total_commits=1
        )
    )
    handler = DeploymentCommitsSyncHandler(
        "org-1", github, code_repo_service, workflow_repo_service
    )
    return handler, github, workflow_repo_service


class TestSync:
    def test_first_deploy_has_no_base_and_unknown_commits(self):
        runs = [_run("r1", 0)]
        handler, github, _ = _handler(runs)
        assert handler.sync() == 1
        assert runs[0].meta["shipped"] == {
            "base": None,
            "status": NO_PREVIOUS_DEPLOY,
            "shas": None,
            "truncated": False,
        }
        github.compare_commits.assert_not_called()

    def test_each_deploy_is_compared_with_the_previous_one(self):
        runs = [_run("r1", 0), _run("r2", 1), _run("r3", 2)]
        handler, github, repo = _handler(runs)
        handler.sync()
        assert [c.args[2:] for c in github.compare_commits.call_args_list] == [
            ("r1", "r2"),
            ("r2", "r3"),
        ]
        assert runs[2].meta["shipped"]["shas"] == ["r2..r3"]
        assert runs[2].meta["revision"] == "r3"  # existing meta kept
        repo.save_repo_workflow_runs.assert_called_once()

    def test_redeploying_the_same_revision_ships_nothing_without_a_call(self):
        runs = [_run("r1", 0), _run("r1", 1)]
        handler, github, _ = _handler(runs)
        handler.sync()
        assert runs[1].meta["shipped"]["status"] == "identical"
        assert runs[1].meta["shipped"]["shas"] == []
        github.compare_commits.assert_not_called()

    def test_already_mapped_runs_are_skipped_but_still_set_the_base(self):
        done = {"base": None, "status": NO_PREVIOUS_DEPLOY, "shas": None}
        runs = [_run("r1", 0, shipped=done), _run("r2", 1)]
        handler, github, _ = _handler(runs)
        assert handler.sync() == 1
        github.compare_commits.assert_called_once()
        assert github.compare_commits.call_args.args[2:] == ("r1", "r2")

    def test_runs_without_a_revision_are_ignored(self):
        runs = [_run("r1", 0), _run(None, 1), _run("r2", 2)]
        handler, github, _ = _handler(runs)
        handler.sync()
        assert "shipped" not in runs[1].meta
        assert github.compare_commits.call_args.args[2:] == ("r1", "r2")

    def test_branches_are_tracked_separately(self):
        runs = [_run("m1", 0, "main"), _run("s1", 1, "staging"), _run("m2", 2, "main")]
        handler, github, _ = _handler(runs)
        handler.sync()
        assert runs[1].meta["shipped"]["status"] == NO_PREVIOUS_DEPLOY
        assert github.compare_commits.call_args.args[2:] == ("m1", "m2")

    def test_a_rollback_ships_no_new_commits(self):
        runs = [_run("new", 0), _run("old", 1)]
        handler, _, _ = _handler(
            runs,
            compare=lambda *a: GithubCompareResult(
                status="behind", commit_shas=[], total_commits=0
            ),
        )
        handler.sync()
        assert runs[1].meta["shipped"]["status"] == "behind"
        assert runs[1].meta["shipped"]["shas"] == []

    def test_progress_is_saved_when_a_later_compare_fails(self):
        calls = {"n": 0}

        def compare(*args):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("GitHub 502")
            return GithubCompareResult("ahead", ["x"], 1)

        runs = [_run("r1", 0), _run("r2", 1), _run("r3", 2)]
        handler, _, repo = _handler(runs, compare=compare)
        handler.sync()  # the error is logged, not raised
        saved = repo.save_repo_workflow_runs.call_args.args[0]
        assert [r.meta["revision"] for r in saved] == ["r1", "r2"]


class TestCommitMapping:
    def _prs(self):
        pr_a = get_pull_request(
            repo_id=REPO_ID,
            number="1",
            state=PullRequestState.MERGED,
            merge_commit_sha="sha-a",
            state_changed_at=T0,
        )
        pr_b = get_pull_request(
            repo_id=REPO_ID,
            number="2",
            state=PullRequestState.MERGED,
            merge_commit_sha="sha-b",
            state_changed_at=T0 + timedelta(hours=1),
        )
        return pr_a, pr_b

    def test_approval_gated_deploy_ships_only_its_own_commit(self):
        pr_a, pr_b = self._prs()
        # Starts after B merged, but deploys A's pipeline revision
        deploy = get_deployment(repo_id=REPO_ID, conducted_at=T0 + timedelta(hours=2))
        mapped = get_commit_mapped_deployments(
            [deploy], {deploy.entity_id: {"shas": ["sha-a"]}}
        )
        result = map_prs_to_commit_mapped_deployments(mapped, [pr_a, pr_b])
        assert result[deploy] == [pr_a]

    def test_batched_deploy_carries_every_pr_it_shipped(self):
        pr_a, pr_b = self._prs()
        deploy = get_deployment(repo_id=REPO_ID)
        mapped = get_commit_mapped_deployments(
            [deploy], {deploy.entity_id: {"shas": ["sha-a", "sha-b", "sha-other"]}}
        )
        assert map_prs_to_commit_mapped_deployments(mapped, [pr_b, pr_a])[deploy] == [
            pr_a,
            pr_b,
        ]

    def test_unknown_commits_fall_back_to_the_time_rule(self):
        first = get_deployment(repo_id=REPO_ID)
        unprocessed = get_deployment(repo_id=REPO_ID)
        pr_merge = get_deployment(repo_id=REPO_ID)
        pr_merge.deployment_type = DeploymentType.PR_MERGE
        mapped = get_commit_mapped_deployments(
            [first, unprocessed, pr_merge],
            {
                first.entity_id: {"shas": None, "status": NO_PREVIOUS_DEPLOY},
                pr_merge.entity_id: {"shas": ["x"]},
            },
        )
        assert mapped == {}

    def test_same_sha_in_another_repo_is_not_matched(self):
        pr_a, _ = self._prs()
        deploy = get_deployment(repo_id="another-repo")
        mapped = get_commit_mapped_deployments(
            [deploy], {deploy.entity_id: {"shas": ["sha-a"]}}
        )
        assert map_prs_to_commit_mapped_deployments(mapped, [pr_a])[deploy] == []
