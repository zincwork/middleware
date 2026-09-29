"""
Tests for turning Shortcut production bugs into incidents with a culprit PR.

Chain: bug labelled `production` -> linked fix PR -> culprit PR it names.
Every link can be missing, and each gap must be reported, not dropped.
"""

from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from mhq.service.incidents.production_bugs import (
    DEFAULT_CULPRIT_FILTERS,
    AttributionStatus,
    ProductionBugAttributor,
    adapt_production_bug_incident,
    extract_culprit_number,
    get_incident_culprit_pr_id,
)
from mhq.service.incidents.models.adapter import adaptIncidentPR
from mhq.service.settings.models import IncidentPRsSetting
from mhq.store.models.code import PullRequestState
from mhq.store.models.incidents import IncidentType
from mhq.store.models.tickets import Ticket, TicketPullRequestMap
from mhq.utils.time import time_now
from tests.factories.models import get_incident
from tests.factories.models.code import get_pull_request

T0 = time_now() - timedelta(days=5)
TEAM_REPO = str(uuid4())
OTHER_REPO = str(uuid4())


def _bug(key="sc-100"):
    return Ticket(
        id=uuid4(),
        org_id=uuid4(),
        provider="shortcut",
        key=key,
        title=f"Checkout broken ({key})",
        ticket_type="bug",
        url=f"https://app.shortcut.com/zinc/story/{key}",
        labels=["production"],
        provider_created_at=T0 + timedelta(hours=3),
        completed_at=T0 + timedelta(hours=9),
    )


def _pr(number, title="Some change", head_branch="feature", repo_id=TEAM_REPO, **kw):
    return get_pull_request(
        repo_id=repo_id,
        number=str(number),
        title=title,
        head_branch=head_branch,
        state=kw.pop("state", PullRequestState.MERGED),
        state_changed_at=kw.pop("state_changed_at", T0),
        url=f"https://github.com/zincwork/mvp-api/pull/{number}",
        **kw,
    )


def _attributor(bugs, links, prs, pr_filter_keeps=None):
    tickets_repo = MagicMock()
    tickets_repo.get_bug_tickets_with_label.return_value = bugs
    tickets_repo.get_pull_request_links_for_tickets.return_value = links
    by_id = {str(pr.id): pr for pr in prs}
    by_number = {(str(pr.repo_id), str(pr.number)): pr for pr in prs}

    def get_prs_by_ids(ids, pr_filter=None):
        found = [by_id[i] for i in ids if i in by_id]
        if pr_filter is not None and pr_filter_keeps is not None:
            found = [pr for pr in found if pr.author in pr_filter_keeps]
        return found

    code_repo = MagicMock()
    code_repo.get_prs_by_ids.side_effect = get_prs_by_ids
    code_repo.get_repo_pr_by_number.side_effect = lambda repo_id, n: by_number.get(
        (str(repo_id), str(n))
    )
    return ProductionBugAttributor(tickets_repo, code_repo), tickets_repo


def _link(bug, pr):
    return TicketPullRequestMap(
        ticket_id=bug.id,
        repo_name="mvp-api",
        pr_number=int(pr.number),
        pull_request_id=pr.id,
    )


def _attribute(attributor, setting=None, pr_filter=None):
    return attributor.attribute("org", [TEAM_REPO], setting, T0, pr_filter)


class TestCulpritExtraction:
    def test_default_patterns(self):
        cases = {
            ("Fix #123: null check on basket", "fix-basket"): "123",
            ("fixes #45", "x"): "45",
            ("Reverted #7 because it broke login", "x"): "7",
            ("Tidy up", "hotfix/678-null-basket"): "678",
            ("Hotfix for basket", "hotfix/basket"): None,
            ("Bump deps (#99)", "deps"): None,  # a PR number, not a culprit
        }
        for (title, branch), expected in cases.items():
            pr = _pr(1, title=title, head_branch=branch)
            assert (
                extract_culprit_number(pr, DEFAULT_CULPRIT_FILTERS) == expected
            ), title

    def test_team_filters_replace_the_defaults(self):
        pr = _pr(1, title="[culprit:321] fix")
        filters = [{"field": "title", "value": r"\[culprit:(\d+)\]"}]
        assert extract_culprit_number(pr, filters) == "321"
        assert extract_culprit_number(_pr(1, title="fixes #5"), filters) is None

    def test_invalid_regex_is_ignored(self):
        assert (
            extract_culprit_number(_pr(1), [{"field": "title", "value": "("}]) is None
        )


class TestAttribution:
    def test_full_chain_is_attributed(self):
        bug = _bug()
        culprit = _pr(40, title="Add discount codes")
        fix = _pr(
            41,
            title="Fix #40 discount rounding",
            state_changed_at=T0 + timedelta(hours=6),
        )
        attributor, _ = _attributor([bug], [_link(bug, fix)], [culprit, fix])
        [result] = _attribute(attributor)
        assert result.status == AttributionStatus.ATTRIBUTED
        assert result.culprit_pr is culprit
        assert result.fix_pr is fix

    def test_bug_with_no_linked_pr(self):
        attributor, _ = _attributor([_bug()], [], [])
        [result] = _attribute(attributor)
        assert result.status == AttributionStatus.NO_FIX_PR

    def test_fix_pr_that_names_no_culprit(self):
        bug = _bug()
        fix = _pr(41, title="Handle empty basket")
        attributor, _ = _attributor([bug], [_link(bug, fix)], [fix])
        [result] = _attribute(attributor)
        assert result.status == AttributionStatus.NO_CULPRIT_NAMED
        assert result.fix_pr is fix

    def test_named_culprit_that_does_not_exist_or_never_merged(self):
        bug = _bug()
        open_pr = _pr(40, state=PullRequestState.OPEN)
        fix = _pr(41, title="Fix #40")
        other = _pr(42, title="fixes #999")
        attributor, _ = _attributor(
            [bug], [_link(bug, fix), _link(bug, other)], [open_pr, fix, other]
        )
        [result] = _attribute(attributor)
        assert result.status == AttributionStatus.CULPRIT_NOT_FOUND

    def test_fix_pr_outside_the_team_repos_is_ignored(self):
        bug = _bug()
        fix = _pr(41, title="Fix #40", repo_id=OTHER_REPO)
        attributor, _ = _attributor([bug], [_link(bug, fix)], [fix])
        [result] = _attribute(attributor)
        assert result.status == AttributionStatus.NO_FIX_PR

    def test_merged_fix_is_preferred_over_an_open_one(self):
        bug = _bug()
        culprit_a, culprit_b = _pr(40), _pr(50)
        open_fix = _pr(41, title="Fix #50", state=PullRequestState.OPEN)
        merged_fix = _pr(42, title="Fix #40", state_changed_at=T0 + timedelta(hours=8))
        attributor, _ = _attributor(
            [bug],
            [_link(bug, open_fix), _link(bug, merged_fix)],
            [culprit_a, culprit_b, open_fix, merged_fix],
        )
        [result] = _attribute(attributor)
        assert result.culprit_pr is culprit_a

    def test_label_comes_from_the_setting(self):
        attributor, tickets_repo = _attributor([], [], [])
        setting = IncidentPRsSetting(
            include_revert_prs=True, filters=[], production_bug_label="prod-incident"
        )
        _attribute(attributor, setting)
        assert (
            tickets_repo.get_bug_tickets_with_label.call_args.args[1] == "prod-incident"
        )
        _attribute(attributor, None)
        assert tickets_repo.get_bug_tickets_with_label.call_args.args[1] == "production"

    def test_squad_filter_keeps_only_bugs_whose_culprit_is_theirs(self):
        bug_ours, bug_theirs = _bug("sc-1"), _bug("sc-2")
        ours = _pr(40, author="ada")
        theirs = _pr(50, author="grace")
        fix_ours, fix_theirs = _pr(41, title="Fix #40"), _pr(51, title="Fix #50")
        attributor, _ = _attributor(
            [bug_ours, bug_theirs],
            [_link(bug_ours, fix_ours), _link(bug_theirs, fix_theirs)],
            [ours, theirs, fix_ours, fix_theirs],
            pr_filter_keeps={"ada"},
        )
        results = _attribute(attributor, pr_filter=MagicMock())
        assert [r.status for r in results] == [
            AttributionStatus.ATTRIBUTED,
            AttributionStatus.CULPRIT_FILTERED_OUT,
        ]


class TestIncidentAdaptation:
    def test_incident_is_keyed_on_the_culprit_with_detection_and_fix_times(self):
        bug = _bug()
        culprit = _pr(40)
        fix = _pr(41, title="Fix #40", state_changed_at=T0 + timedelta(hours=6))
        attributor, _ = _attributor([bug], [_link(bug, fix)], [culprit, fix])
        [attribution] = _attribute(attributor)
        incident = adapt_production_bug_incident(attribution)
        assert incident.key == str(culprit.id)
        assert incident.incident_type == IncidentType.PRODUCTION_BUG
        assert incident.creation_date == bug.provider_created_at
        assert incident.resolved_date == fix.state_changed_at
        assert incident.status == "resolved"
        assert incident.meta["ticket_key"] == "sc-100"
        assert get_incident_culprit_pr_id(incident) == str(culprit.id)


class TestCulpritOfExistingIncidents:
    def test_revert_incident_from_the_incidents_sync(self):
        incident = get_incident(
            incident_type=IncidentType.REVERT_PR,
            key="ignored",
            meta={"original_pr": {"id": "pr-123"}},
        )
        assert get_incident_culprit_pr_id(incident) == "pr-123"

    def test_incident_built_from_incident_pr_filters(self):
        culprit, resolution = _pr(40), _pr(41)
        incident = adaptIncidentPR(culprit, resolution)
        assert get_incident_culprit_pr_id(incident) == str(culprit.id)

    def test_incident_service_incident_has_no_culprit(self):
        assert get_incident_culprit_pr_id(get_incident()) is None
