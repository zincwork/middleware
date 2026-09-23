"""
Tests for the Shortcut ticket ETL handler.

The history fixture below is the real, unedited shape returned for Zinc story
sc-8765 ("Credit Check: PII in Rollbar log"), which is why it exercises the
awkward parts: an initial `create` carrying a flat workflow_state_id, later
`update`s nesting it under `changes`, a custom-field change with no state at
all, and branch and pull-request events arriving in separate entries.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from mhq.exapi.models.shortcut import (
    ShortcutHistoryEntry,
    ShortcutStory,
    ShortcutWorkflowState,
)
from mhq.exapi.shortcut import parse_shortcut_datetime
from mhq.service.tickets.sync.etl_shortcut_handler import ShortcutETLHandler
from mhq.store.models.tickets import TicketStateType

ORG_ID = "9c8f2a24-1c1f-4b7f-9a2e-4a26d1c1f0aa"
SKIPPER = "677be3f3-b1d5-488b-b5ba-9d3ca7d5012b"

WORKFLOW_STATES = {
    "500000073": ShortcutWorkflowState("500000073", "Backlog", "backlog", "500000005", "Standard"),
    "500000008": ShortcutWorkflowState("500000008", "In Progress", "started", "500000005", "Standard"),
    "500000009": ShortcutWorkflowState("500000009", "Review Requested", "started", "500000005", "Standard"),
    "500000010": ShortcutWorkflowState("500000010", "Done", "done", "500000005", "Standard"),
    # Trailing space is genuinely in Zinc's workflow.
    "500000034": ShortcutWorkflowState("500000034", "Cancelled ", "done", "500000005", "Standard"),
}


def _state_ref(state_id, name, state_type):
    return {
        "id": int(state_id),
        "entity_type": "workflow-state",
        "name": name,
        "type": state_type,
    }


REAL_HISTORY = [
    # create — flat workflow_state_id
    ShortcutHistoryEntry(
        changed_at=parse_shortcut_datetime("2026-08-26T14:19:45.320Z"),
        actor_name="Alisdair Little",
        actions=[
            {
                "id": 8765,
                "action": "create",
                "story_type": "bug",
                "name": "Credit Check: PII in Rollbar log",
                "workflow_state_id": 500000073,
                "group_id": SKIPPER,
            }
        ],
        references=[_state_ref("500000073", "Backlog", "backlog")],
    ),
    # a custom-field change — no state, must be ignored
    ShortcutHistoryEntry(
        changed_at=parse_shortcut_datetime("2026-09-01T11:04:51.959Z"),
        actor_name="Rob Leonard",
        actions=[
            {
                "id": 8765,
                "action": "update",
                "changes": {"custom_field_value_ids": {"adds": ["x"]}},
            }
        ],
        references=[],
    ),
    # owner change — also no state
    ShortcutHistoryEntry(
        changed_at=parse_shortcut_datetime("2026-09-01T12:17:55.359Z"),
        actor_name="Emily Yoon",
        actions=[
            {"id": 8765, "action": "update", "changes": {"owner_ids": {"adds": ["y"]}}}
        ],
        references=[],
    ),
    # branch push AND a state change in the same entry
    ShortcutHistoryEntry(
        changed_at=parse_shortcut_datetime("2026-09-01T14:55:46.242Z"),
        actor_name="Emily Yoon",
        actions=[
            {
                "id": 500006582,
                "entity_type": "branch",
                "action": "push",
                "name": "blue/bug/sc-8765/pii-issue",
                "url": "https://github.com/zincwork/mvp-api/tree/blue/bug/sc-8765/pii-issue",
            },
            {
                "id": 8765,
                "action": "update",
                "changes": {
                    "started": {"new": True, "old": False},
                    "workflow_state_id": {"new": 500000008, "old": 500000073},
                },
            },
        ],
        references=[
            _state_ref("500000008", "In Progress", "started"),
            _state_ref("500000073", "Backlog", "backlog"),
        ],
    ),
    # pull request opened
    ShortcutHistoryEntry(
        changed_at=parse_shortcut_datetime("2026-09-01T15:51:24.566Z"),
        actor_name="Emily Yoon",
        actions=[
            {
                "id": 500006583,
                "entity_type": "pull-request",
                "action": "update",
                "number": 6906,
                "title": "pii issue",
                "url": "https://github.com/zincwork/mvp-api/pull/6906",
            },
            {
                "id": 8765,
                "action": "update",
                "changes": {"workflow_state_id": {"new": 500000009, "old": 500000008}},
            },
        ],
        references=[
            _state_ref("500000009", "Review Requested", "started"),
            _state_ref("500000008", "In Progress", "started"),
        ],
    ),
    # done
    ShortcutHistoryEntry(
        changed_at=parse_shortcut_datetime("2026-09-02T15:57:59.937Z"),
        actor_name="Emily Yoon",
        actions=[
            {
                "id": 500006583,
                "entity_type": "pull-request",
                "action": "close",
                "number": 6906,
                "url": "https://github.com/zincwork/mvp-api/pull/6906",
            },
            {
                "id": 8765,
                "action": "update",
                "changes": {
                    "workflow_state_id": {"new": 500000010, "old": 500000009},
                    "completed": {"new": True, "old": False},
                },
            },
        ],
        references=[
            _state_ref("500000010", "Done", "done"),
            _state_ref("500000009", "Review Requested", "started"),
        ],
    ),
]


def _handler(existing_ticket=None):
    repo = MagicMock()
    repo.get_ticket_by_idempotency_key.return_value = existing_ticket
    handler = ShortcutETLHandler(MagicMock(), repo)
    handler._workflow_states = WORKFLOW_STATES
    return handler


def _story(**overrides):
    defaults = dict(
        id="8765",
        name="Credit Check: PII in Rollbar log",
        story_type="bug",
        app_url="https://app.shortcut.com/zinc-1/story/8765",
        team_id=SKIPPER,
        epic_id=None,
        iteration_id=None,
        parent_story_id=None,
        workflow_id="500000005",
        workflow_state_id="500000010",
        owner_id="669e6813-7894-4ee8-a284-31605de9be7b",
        requested_by_id="6a43da2a-d5ab-4f57-8827-0e4586aab939",
        estimate=None,
        labels=["quality-discovery"],
        created_at=parse_shortcut_datetime("2026-08-26T14:19:45.320Z"),
        updated_at=parse_shortcut_datetime("2026-09-02T15:57:59.937Z"),
        started_at=parse_shortcut_datetime("2026-09-01T14:55:46.242Z"),
        completed_at=parse_shortcut_datetime("2026-09-02T15:57:59.937Z"),
        is_archived=False,
        raw={},
    )
    defaults.update(overrides)
    return ShortcutStory(**defaults)


class TestStateChangeExtraction:
    def test_create_action_yields_the_initial_state(self):
        assert ShortcutETLHandler._extract_state_change(
            {"action": "create", "workflow_state_id": 500000073}
        ) == (None, "500000073")

    def test_update_action_yields_from_and_to(self):
        assert ShortcutETLHandler._extract_state_change(
            {"changes": {"workflow_state_id": {"new": 500000008, "old": 500000073}}}
        ) == ("500000073", "500000008")

    def test_non_state_changes_are_ignored(self):
        assert (
            ShortcutETLHandler._extract_state_change(
                {"changes": {"owner_ids": {"adds": ["x"]}}}
            )
            is None
        )
        assert ShortcutETLHandler._extract_state_change({"action": "update"}) is None

    def test_a_branch_push_is_not_a_state_change(self):
        assert (
            ShortcutETLHandler._extract_state_change(
                {"entity_type": "branch", "action": "push", "name": "x"}
            )
            is None
        )


class TestRepoNameFromUrl:
    def test_pull_request_url(self):
        assert (
            ShortcutETLHandler._repo_name_from_url(
                "https://github.com/zincwork/mvp-api/pull/6906"
            )
            == "mvp-api"
        )

    def test_branch_tree_url(self):
        assert (
            ShortcutETLHandler._repo_name_from_url(
                "https://github.com/zincwork/mvp-app/tree/blue/feat/x"
            )
            == "mvp-app"
        )

    def test_returns_none_rather_than_guessing(self):
        assert ShortcutETLHandler._repo_name_from_url(None) is None
        assert ShortcutETLHandler._repo_name_from_url("") is None
        assert ShortcutETLHandler._repo_name_from_url("https://gitlab.com/a/b") is None
        assert ShortcutETLHandler._repo_name_from_url("https://github.com/zincwork") is None


class TestTransitions:
    def test_the_full_real_history_produces_four_transitions(self):
        transitions = _handler()._adapt_transitions("t1", REAL_HISTORY)
        assert [(t.from_state, t.to_state) for t in transitions] == [
            (None, "Backlog"),
            ("Backlog", "In Progress"),
            ("In Progress", "Review Requested"),
            ("Review Requested", "Done"),
        ]

    def test_state_types_come_through(self):
        transitions = _handler()._adapt_transitions("t1", REAL_HISTORY)
        assert [t.to_state_type for t in transitions] == [
            "backlog",
            "started",
            "started",
            "done",
        ]

    def test_transitions_are_ordered_by_time(self):
        transitions = _handler()._adapt_transitions("t1", list(reversed(REAL_HISTORY)))
        times = [t.changed_at for t in transitions]
        assert times == sorted(times)

    def test_actor_is_recorded(self):
        transitions = _handler()._adapt_transitions("t1", REAL_HISTORY)
        assert transitions[0].actor == "Alisdair Little"
        assert transitions[-1].actor == "Emily Yoon"

    def test_review_wait_is_measurable(self):
        # The whole point of storing transitions: time in Review Requested.
        transitions = _handler()._adapt_transitions("t1", REAL_HISTORY)
        by_state = {t.to_state: t.changed_at for t in transitions}
        in_review = by_state["Done"] - by_state["Review Requested"]
        assert in_review == timedelta(hours=24, minutes=6, seconds=35, microseconds=371000)

    def test_entries_without_a_timestamp_are_skipped(self):
        entry = ShortcutHistoryEntry(
            changed_at=None,
            actor_name="x",
            actions=[{"action": "create", "workflow_state_id": 500000073}],
            references=[],
        )
        assert _handler()._adapt_transitions("t1", [entry]) == []

    def test_a_renamed_or_deleted_state_still_resolves_from_references(self):
        # History carries state names inline, so a state since removed from the
        # workflow still resolves.
        entry = ShortcutHistoryEntry(
            changed_at=parse_shortcut_datetime("2026-09-01T10:00:00Z"),
            actor_name="x",
            actions=[{"changes": {"workflow_state_id": {"new": 999, "old": None}}}],
            references=[_state_ref("999", "Retired State", "started")],
        )
        transitions = _handler()._adapt_transitions("t1", [entry])
        assert transitions[0].to_state == "Retired State"
        assert transitions[0].to_state_type == "started"

    def test_an_unknown_state_with_no_reference_degrades_gracefully(self):
        entry = ShortcutHistoryEntry(
            changed_at=parse_shortcut_datetime("2026-09-01T10:00:00Z"),
            actor_name="x",
            actions=[{"changes": {"workflow_state_id": {"new": 12345, "old": None}}}],
            references=[],
        )
        transitions = _handler()._adapt_transitions("t1", [entry])
        assert transitions[0].to_state is None
        assert transitions[0].to_state_type == TicketStateType.UNKNOWN.value


class TestGitLinks:
    def test_branch_and_pr_are_paired_into_one_link(self):
        links = _handler()._adapt_git_links("t1", REAL_HISTORY)
        assert len(links) == 1
        link = links[0]
        assert link.repo_name == "mvp-api"
        assert link.pr_number == 6906
        assert link.branch_name == "blue/bug/sc-8765/pii-issue"

    def test_the_same_pr_appearing_twice_is_deduplicated(self):
        # PR 6906 appears as both `update` and `close` in the real history.
        links = _handler()._adapt_git_links("t1", REAL_HISTORY)
        assert len([l for l in links if l.pr_number == 6906]) == 1

    def test_a_branch_with_no_pr_yields_no_link(self):
        # There is nothing to join to yet, so no row.
        branch_only = [REAL_HISTORY[0], REAL_HISTORY[3]]
        # entry 3 contains a state change plus the branch push; strip the PR
        # entries and the link list should be empty.
        links = _handler()._adapt_git_links("t1", branch_only)
        assert links == []

    def test_prs_in_different_repos_are_separate_links(self):
        entry = ShortcutHistoryEntry(
            changed_at=parse_shortcut_datetime("2026-09-01T10:00:00Z"),
            actor_name="x",
            actions=[
                {
                    "entity_type": "pull-request",
                    "number": 1,
                    "url": "https://github.com/zincwork/mvp-api/pull/1",
                },
                {
                    "entity_type": "pull-request",
                    "number": 2,
                    "url": "https://github.com/zincwork/mvp-app/pull/2",
                },
            ],
            references=[],
        )
        links = _handler()._adapt_git_links("t1", [entry])
        assert {(l.repo_name, l.pr_number) for l in links} == {
            ("mvp-api", 1),
            ("mvp-app", 2),
        }


class TestCancelled:
    def test_trailing_space_is_handled(self):
        # Zinc's workflow literally contains "Cancelled " with a trailing space.
        assert ShortcutETLHandler._is_cancelled("Cancelled ") is True
        assert ShortcutETLHandler._is_cancelled("cancelled") is True
        assert ShortcutETLHandler._is_cancelled("Canceled") is True

    def test_done_is_not_cancelled(self):
        assert ShortcutETLHandler._is_cancelled("Done") is False
        assert ShortcutETLHandler._is_cancelled(None) is False


class TestTicketAdaptation:
    def test_maps_the_core_fields(self):
        ticket = _handler()._adapt_story(ORG_ID, _story())
        assert ticket.org_id == ORG_ID
        assert ticket.provider == "shortcut"
        assert ticket.idempotency_key == "8765"
        assert ticket.key == "sc-8765"
        assert ticket.ticket_type == "bug"
        assert ticket.provider_team_id == SKIPPER
        assert ticket.current_state == "Done"
        assert ticket.current_state_type == "done"
        assert ticket.is_complete is True
        assert ticket.is_cancelled is False
        assert ticket.labels == ["quality-discovery"]

    def test_cancelled_story_is_flagged_and_not_treated_as_delivered(self):
        ticket = _handler()._adapt_story(
            ORG_ID, _story(workflow_state_id="500000034", completed_at=None)
        )
        assert ticket.current_state == "Cancelled "
        assert ticket.current_state_type == "done"
        assert ticket.is_cancelled is True
        assert ticket.is_complete is False

    def test_subtask_is_flagged_so_it_can_be_excluded_from_counting(self):
        ticket = _handler()._adapt_story(ORG_ID, _story(parent_story_id="8485"))
        assert ticket.is_subtask is True
        assert ticket.parent_idempotency_key == "8485"

    def test_top_level_story_is_not_a_subtask(self):
        assert _handler()._adapt_story(ORG_ID, _story()).is_subtask is False

    def test_existing_ticket_is_updated_in_place(self):
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.created_at = parse_shortcut_datetime("2026-01-01T00:00:00Z")
        ticket = _handler(existing_ticket=existing)._adapt_story(ORG_ID, _story())
        assert ticket.id == "existing-uuid"
        assert ticket.created_at == existing.created_at

    def test_unknown_story_type_does_not_crash(self):
        ticket = _handler()._adapt_story(ORG_ID, _story(story_type=None))
        assert ticket.ticket_type == "unknown"

    def test_priority_is_read_out_of_custom_fields(self):
        story = _story(
            raw={"custom_fields": [{"field_name": "Priority", "value": "High"}]}
        )
        assert _handler()._adapt_story(ORG_ID, story).priority == "High"

    def test_missing_priority_is_none(self):
        assert _handler()._adapt_story(ORG_ID, _story()).priority is None


class TestBookmark:
    def test_uses_the_newest_update_minus_an_overlap(self):
        stories = [
            _story(updated_at=parse_shortcut_datetime("2026-09-01T00:00:00Z")),
            _story(updated_at=parse_shortcut_datetime("2026-09-05T12:00:00Z")),
        ]
        fallback = parse_shortcut_datetime("2026-01-01T00:00:00Z")
        bookmark = _handler()._get_new_bookmark(stories, fallback)
        assert bookmark == parse_shortcut_datetime("2026-09-05T11:00:00Z")

    def test_falls_back_when_nothing_has_a_timestamp(self):
        fallback = parse_shortcut_datetime("2026-01-01T00:00:00Z")
        assert (
            _handler()._get_new_bookmark([_story(updated_at=None)], fallback)
            == fallback
        )
