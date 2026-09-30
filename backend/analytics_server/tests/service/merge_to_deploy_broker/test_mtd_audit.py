"""
Tests for the merge-to-deploy audit: does the replay agree with what is
cached, and does it say the right thing when it does not.

This exists because of a real incident: the CircleCI timestamp-parsing bug
(fixed in mhq/exapi/circle_ci.py, 2026-09-29) silently dropped some real
deployments before they were ever recorded, so PRs merged around those gaps
got permanently cached against whatever deployment the matcher found next --
in one observed case, 4 weeks 5 days later than the deploy that actually
shipped them (GitHub PR zincwork/mvp-api#6679). merge_to_deploy is a
permanent, NULL-gated cache (see mtd_handler.py), so backfilling the missing
deployment does nothing for a PR already matched to the wrong one -- this
audit is what finds those.

The real DeploymentPRMapperService is used throughout, not a mock of it --
the point is to prove the audit reads the SAME matching decision the real
cache handler would make, not a reimplementation that could quietly diverge.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import mhq.service.merge_to_deploy_broker.mtd_audit as mtd_audit
from mhq.service.deployments import DeploymentPRMapperService
from mhq.service.merge_to_deploy_broker.mtd_audit import (
    MergeToDeployFinding,
    MergeToDeployFixService,
    compute_fix_outcome,
    replay_merge_to_deploy,
)
from mhq.store.models.code.enums import PullRequestState

MAPPER = DeploymentPRMapperService()

# The real deploy that shipped PR #6679, per CircleCI.
CORRECT_DEPLOY_TIME = datetime(2026, 7, 2, 9, 51, tzinfo=timezone.utc)


class FakePR:
    """DeploymentPRGraph puts PRs in a set, exactly like the real
    PullRequest model (which defines __hash__/__eq__ on id) allows. A plain
    SimpleNamespace does not support that -- it defines __eq__ without
    __hash__, so instances are unhashable and the branch walk raises."""

    def __init__(
        self, id, number, merged_at, merge_to_deploy=None,
        base_branch="main", head_branch="feature", state=None,
    ):
        self.id = id
        self.number = number
        self.url = f"https://github.com/zincwork/mvp-api/pull/{number}"
        self.state = state if state is not None else PullRequestState.MERGED
        self.base_branch = base_branch
        self.head_branch = head_branch
        self.state_changed_at = merged_at
        self.merge_to_deploy = merge_to_deploy

    def __eq__(self, other):
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)


def pr(
    id, number, merged_at, merge_to_deploy=None,
    base_branch="main", head_branch="feature", state=None,
):
    return FakePR(id, number, merged_at, merge_to_deploy, base_branch, head_branch, state)


def run(id, conducted_at, head_branch="main"):
    return SimpleNamespace(id=id, conducted_at=conducted_at, head_branch=head_branch)


def test_pr_6679_style_misattribution_is_caught():
    """The exact reported bug: cached against a deploy 4w5d later than the
    real one, which has since been recovered by a backfill."""
    merged_at = CORRECT_DEPLOY_TIME - timedelta(hours=9)
    stale_seconds = int(
        (CORRECT_DEPLOY_TIME + timedelta(weeks=4, days=5) - merged_at).total_seconds()
    )
    pr_6679 = pr("pr-6679", "6679", merged_at, merge_to_deploy=stale_seconds)

    recovered_run = run("run-real", CORRECT_DEPLOY_TIME)
    distant_run = run("run-distant", CORRECT_DEPLOY_TIME + timedelta(weeks=4, days=5))

    findings = replay_merge_to_deploy([recovered_run, distant_run], [pr_6679], MAPPER)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.status == "mismatch"
    assert finding.recomputed_seconds == int(
        (CORRECT_DEPLOY_TIME - merged_at).total_seconds()
    )
    assert finding.recomputed_run_id == "run-real"
    assert finding.cached_seconds == stale_seconds


def test_correctly_cached_pr_is_not_flagged():
    merged_at = CORRECT_DEPLOY_TIME - timedelta(hours=2)
    correct_seconds = int((CORRECT_DEPLOY_TIME - merged_at).total_seconds())
    pr_ok = pr("pr-ok", "7000", merged_at, merge_to_deploy=correct_seconds)

    findings = replay_merge_to_deploy([run("run-b", CORRECT_DEPLOY_TIME)], [pr_ok], MAPPER)

    assert findings == []


def test_pr_with_no_reachable_deploy_yet_is_flagged_distinctly():
    """The deploy genuinely hasn't been recovered yet -- this needs a
    backfill, not a null-and-recompute, so it must not look like an ordinary
    mismatch."""
    pr_missing = pr(
        "pr-missing", "7001", CORRECT_DEPLOY_TIME - timedelta(days=100),
        merge_to_deploy=999999,
    )
    # Only a deploy that predates the merge exists.
    findings = replay_merge_to_deploy(
        [run("run-c", CORRECT_DEPLOY_TIME - timedelta(days=200))], [pr_missing], MAPPER
    )

    assert len(findings) == 1
    assert findings[0].status == "cached_value_has_no_replay_match"
    assert findings[0].recomputed_seconds is None


def test_pr_never_cached_is_not_flagged():
    """merge_to_deploy is still NULL -- the real cache handler will pick
    this PR up on its own next sync; the audit has nothing to say about it."""
    pr_new = pr("pr-new", "7002", CORRECT_DEPLOY_TIME - timedelta(hours=1))
    findings = replay_merge_to_deploy([run("run-d", CORRECT_DEPLOY_TIME)], [pr_new], MAPPER)

    assert findings == []


def test_unmerged_pr_state_is_never_matched():
    """A guard against the branch-graph reading state incorrectly -- only
    DeploymentPRMapperService's own MERGED check should decide this, and it
    should still leave a stale cached value flagged rather than silently
    trusting it."""
    open_pr = pr(
        "pr-open", "7003", CORRECT_DEPLOY_TIME - timedelta(hours=1),
        merge_to_deploy=12345, state=PullRequestState.OPEN,
    )
    findings = replay_merge_to_deploy([run("run-e", CORRECT_DEPLOY_TIME)], [open_pr], MAPPER)

    assert len(findings) == 1
    assert findings[0].status == "cached_value_has_no_replay_match"


def test_run_order_is_load_bearing():
    """Mutation guard: if the caller ever stops sorting runs ascending by
    conducted_at before calling this, the match silently changes. Proves the
    tests above are not passing by accident of a lucky default order."""
    merged_at = CORRECT_DEPLOY_TIME - timedelta(hours=9)
    stale_seconds = int(
        (CORRECT_DEPLOY_TIME + timedelta(weeks=4, days=5) - merged_at).total_seconds()
    )
    pr_6679 = pr("pr-6679", "6679", merged_at, merge_to_deploy=stale_seconds)
    recovered_run = run("run-real", CORRECT_DEPLOY_TIME)
    distant_run = run("run-distant", CORRECT_DEPLOY_TIME + timedelta(weeks=4, days=5))

    correct_order = replay_merge_to_deploy([recovered_run, distant_run], [pr_6679], MAPPER)
    wrong_order = replay_merge_to_deploy([distant_run, recovered_run], [pr_6679], MAPPER)

    assert correct_order != wrong_order


# ============================================================
# The fix service: does it write what the audit found, and nothing else?
# ============================================================


class FakeCodeRepoService:
    """Records what would be persisted, without touching a database."""

    def __init__(self):
        self.update_prs_calls = []

    def update_prs(self, prs):
        self.update_prs_calls.append(list(prs))


def _mismatch_finding(cached_seconds=99999999, recomputed_seconds=32400):
    return MergeToDeployFinding(
        pr_id="pr-x",
        pr_number="6679",
        pr_url="https://github.com/zincwork/mvp-api/pull/6679",
        state_changed_at=CORRECT_DEPLOY_TIME - timedelta(hours=9),
        cached_seconds=cached_seconds,
        recomputed_seconds=recomputed_seconds,
        recomputed_run_id="run-real",
        recomputed_run_conducted_at=CORRECT_DEPLOY_TIME,
        status="mismatch",
    )


def _no_match_finding():
    return MergeToDeployFinding(
        pr_id="pr-y",
        pr_number="7001",
        pr_url="https://github.com/zincwork/mvp-api/pull/7001",
        state_changed_at=CORRECT_DEPLOY_TIME - timedelta(days=100),
        cached_seconds=999999,
        recomputed_seconds=None,
        recomputed_run_id=None,
        recomputed_run_conducted_at=None,
        status="cached_value_has_no_replay_match",
    )


def test_compute_fix_outcome_writes_the_replays_own_recomputed_value():
    """No second computation — the fix applies exactly what the audit
    already found, so there is nothing for the two to disagree about."""
    finding = _mismatch_finding()
    outcome = compute_fix_outcome(finding)

    assert outcome.action == "corrected"
    assert outcome.new_value == finding.recomputed_seconds


def test_compute_fix_outcome_nulls_when_no_deploy_is_reachable_yet():
    """No correct value exists to write -- null is the honest state, and is
    what makes the PR eligible again once a backfill recovers the deploy."""
    finding = _no_match_finding()
    outcome = compute_fix_outcome(finding)

    assert outcome.action == "nulled_pending_backfill"
    assert outcome.new_value is None


def test_fix_repo_dry_run_never_calls_update_prs(monkeypatch):
    merged_at = CORRECT_DEPLOY_TIME - timedelta(hours=9)
    stale_seconds = int(
        (CORRECT_DEPLOY_TIME + timedelta(weeks=4, days=5) - merged_at).total_seconds()
    )
    pr_6679 = pr("pr-6679", "6679", merged_at, merge_to_deploy=stale_seconds)
    recovered_run = run("run-real", CORRECT_DEPLOY_TIME)
    distant_run = run("run-distant", CORRECT_DEPLOY_TIME + timedelta(weeks=4, days=5))

    monkeypatch.setattr(mtd_audit, "_get_all_successful_runs", lambda repo_id: [recovered_run, distant_run])
    monkeypatch.setattr(mtd_audit, "_get_all_merged_prs", lambda repo_id: [pr_6679])

    fake_code_repo_service = FakeCodeRepoService()
    service = MergeToDeployFixService(
        deployment_pr_mapper_service=MAPPER, code_repo_service=fake_code_repo_service
    )

    outcomes = service.fix_repo("repo-1", dry_run=True)

    assert len(outcomes) == 1
    assert outcomes[0].action == "corrected"
    assert fake_code_repo_service.update_prs_calls == []
    # The PR object itself must be untouched in dry-run mode too, not just
    # the database call -- a caller re-reading `pr_6679` afterwards should
    # see the original (stale) value.
    assert pr_6679.merge_to_deploy == stale_seconds


def test_fix_repo_applies_and_persists_when_not_dry_run(monkeypatch):
    merged_at = CORRECT_DEPLOY_TIME - timedelta(hours=9)
    stale_seconds = int(
        (CORRECT_DEPLOY_TIME + timedelta(weeks=4, days=5) - merged_at).total_seconds()
    )
    correct_seconds = int((CORRECT_DEPLOY_TIME - merged_at).total_seconds())
    pr_6679 = pr("pr-6679", "6679", merged_at, merge_to_deploy=stale_seconds)
    recovered_run = run("run-real", CORRECT_DEPLOY_TIME)
    distant_run = run("run-distant", CORRECT_DEPLOY_TIME + timedelta(weeks=4, days=5))

    monkeypatch.setattr(mtd_audit, "_get_all_successful_runs", lambda repo_id: [recovered_run, distant_run])
    monkeypatch.setattr(mtd_audit, "_get_all_merged_prs", lambda repo_id: [pr_6679])

    fake_code_repo_service = FakeCodeRepoService()
    service = MergeToDeployFixService(
        deployment_pr_mapper_service=MAPPER, code_repo_service=fake_code_repo_service
    )

    outcomes = service.fix_repo("repo-1", dry_run=False)

    assert len(outcomes) == 1
    assert outcomes[0].new_value == correct_seconds
    # The in-memory PR object was actually mutated...
    assert pr_6679.merge_to_deploy == correct_seconds
    # ...and handed to the exact same persistence method the real cache
    # handler itself uses, exactly once, with exactly this PR.
    assert len(fake_code_repo_service.update_prs_calls) == 1
    assert fake_code_repo_service.update_prs_calls[0] == [pr_6679]


def test_fix_repo_nulls_unreachable_prs_when_not_dry_run(monkeypatch):
    pr_missing = pr(
        "pr-missing", "7001", CORRECT_DEPLOY_TIME - timedelta(days=100),
        merge_to_deploy=999999,
    )
    monkeypatch.setattr(
        mtd_audit, "_get_all_successful_runs",
        lambda repo_id: [run("run-c", CORRECT_DEPLOY_TIME - timedelta(days=200))],
    )
    monkeypatch.setattr(mtd_audit, "_get_all_merged_prs", lambda repo_id: [pr_missing])

    fake_code_repo_service = FakeCodeRepoService()
    service = MergeToDeployFixService(
        deployment_pr_mapper_service=MAPPER, code_repo_service=fake_code_repo_service
    )

    outcomes = service.fix_repo("repo-1", dry_run=False)

    assert len(outcomes) == 1
    assert outcomes[0].action == "nulled_pending_backfill"
    assert pr_missing.merge_to_deploy is None
    assert fake_code_repo_service.update_prs_calls[0] == [pr_missing]


def test_fix_repo_with_no_findings_never_calls_update_prs(monkeypatch):
    """No noise, no accidental empty writes, on a repo with nothing wrong."""
    merged_at = CORRECT_DEPLOY_TIME - timedelta(hours=2)
    correct_seconds = int((CORRECT_DEPLOY_TIME - merged_at).total_seconds())
    pr_ok = pr("pr-ok", "7000", merged_at, merge_to_deploy=correct_seconds)

    monkeypatch.setattr(
        mtd_audit, "_get_all_successful_runs", lambda repo_id: [run("run-b", CORRECT_DEPLOY_TIME)]
    )
    monkeypatch.setattr(mtd_audit, "_get_all_merged_prs", lambda repo_id: [pr_ok])

    fake_code_repo_service = FakeCodeRepoService()
    service = MergeToDeployFixService(
        deployment_pr_mapper_service=MAPPER, code_repo_service=fake_code_repo_service
    )

    outcomes = service.fix_repo("repo-1", dry_run=False)

    assert outcomes == []
    assert fake_code_repo_service.update_prs_calls == []
