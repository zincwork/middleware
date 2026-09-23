"""
Shortcut ticket ETL handler.

Turns Shortcut stories plus their history into Ticket, TicketStateTransition
and TicketPullRequestMap rows.

Three things are worth knowing about the parsing:

1. State transitions come from history, not from the story. The story tells
   you where something is now; only history tells you how long it sat in
   review. Zinc's workflow has `Review Requested` as its own state, so this is
   where the most useful metric comes from.

2. `Cancelled` is a `done`-type state. Treating it as completed would inflate
   throughput and flatter cycle time, so it is flagged separately.

3. Shortcut records branch pushes and pull request events in the same history
   feed, with the PR number. That gives a direct ticket-to-PR join instead of
   a heuristic — the one thing that ties the project half to the code half.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from mhq.exapi.models.shortcut import (
    ShortcutGitLink,
    ShortcutHistoryEntry,
    ShortcutStory,
    ShortcutWorkflowState,
)
from mhq.exapi.shortcut import ShortcutApiService, parse_shortcut_datetime
from mhq.service.tickets.models import RawTicketBundle
from mhq.service.tickets.sync.etl_provider_handler import TicketProviderETLHandler
from mhq.store.models.tickets import (
    Ticket,
    TicketPullRequestMap,
    TicketProviders,
    TicketStateTransition,
    TicketStateType,
    TicketType,
)
from mhq.store.repos.tickets import TicketsRepoService
from mhq.utils.log import LOG
from mhq.utils.string import uuid4_str
from mhq.utils.time import time_now

# State names that mean "abandoned" rather than "delivered". Matched
# case-insensitively and trimmed, because Zinc's workflows contain
# "Cancelled " with a trailing space.
CANCELLED_STATE_NAMES = {"cancelled", "canceled", "disreguarded", "disregarded"}

# Safety net for the very first sync so it cannot walk the entire workspace.
DEFAULT_BACKFILL_DAYS = 120


class ShortcutETLHandler(TicketProviderETLHandler):
    def __init__(
        self,
        api_service: ShortcutApiService,
        tickets_repo_service: TicketsRepoService,
        backfill_days: int = DEFAULT_BACKFILL_DAYS,
    ):
        self._api = api_service
        self._tickets_repo_service = tickets_repo_service
        self._backfill_days = backfill_days
        self._provider = TicketProviders.SHORTCUT.value
        self._workflow_states: Dict[str, ShortcutWorkflowState] = {}

    def check_pat_validity(self) -> bool:
        if not self._api.check_token():
            raise Exception("Shortcut API token is invalid")
        return True

    def get_tickets(
        self, org_id: str, bookmark: datetime
    ) -> Tuple[List[RawTicketBundle], datetime]:

        since = bookmark or (time_now() - timedelta(days=self._backfill_days))

        # Cache the state map once per sync: every story needs it and it is a
        # single call.
        self._workflow_states = self._api.get_workflow_states()

        query = f"updated:{since.strftime('%Y-%m-%d')}..*"
        stories = self._api.search_stories(query)

        if not stories:
            LOG.info(
                f"[Shortcut Sync] No stories updated since {since.date()} "
                f"for org {org_id}"
            )
            return [], since

        bundles: List[RawTicketBundle] = []
        for story in stories:
            if story.is_archived:
                continue
            try:
                bundles.append(self._build_bundle(org_id, story))
            except Exception as e:
                # One malformed story must not stop the sync.
                LOG.error(
                    f"[Shortcut Sync] Skipping story {story.id}: {str(e)}"
                )
                continue

        return bundles, self._get_new_bookmark(stories, since)

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def _build_bundle(self, org_id: str, story: ShortcutStory) -> RawTicketBundle:
        history = self._api.get_story_history(story.id)

        ticket = self._adapt_story(org_id, story)
        transitions = self._adapt_transitions(str(ticket.id), history)
        links = self._adapt_git_links(str(ticket.id), history)

        return RawTicketBundle(
            ticket=ticket, transitions=transitions, pull_request_links=links
        )

    def _adapt_story(self, org_id: str, story: ShortcutStory) -> Ticket:
        existing = self._tickets_repo_service.get_ticket_by_idempotency_key(
            org_id, self._provider, story.id
        )
        ticket_id = existing.id if existing else uuid4_str()

        state = self._workflow_states.get(story.workflow_state_id or "")
        state_name = state.name if state else None
        state_type = (
            TicketStateType.from_provider(state.state_type).value
            if state
            else TicketStateType.UNKNOWN.value
        )

        return Ticket(
            id=ticket_id,
            org_id=org_id,
            provider=self._provider,
            idempotency_key=story.id,
            key=story.key,
            title=story.name,
            ticket_type=TicketType.from_provider(story.story_type).value,
            url=story.app_url,
            provider_team_id=story.team_id,
            provider_epic_id=story.epic_id,
            provider_iteration_id=story.iteration_id,
            parent_idempotency_key=story.parent_story_id,
            is_subtask=bool(story.parent_story_id),
            owner=story.owner_id,
            requester=story.requested_by_id,
            estimate=story.estimate,
            workflow_id=story.workflow_id,
            current_state=state_name,
            current_state_type=state_type,
            priority=self._extract_priority(story),
            labels=story.labels or [],
            provider_created_at=story.created_at,
            provider_updated_at=story.updated_at,
            started_at=story.started_at,
            completed_at=story.completed_at,
            is_complete=bool(story.completed_at),
            is_cancelled=self._is_cancelled(state_name),
            meta=self._build_meta(story, state),
            created_at=existing.created_at if existing else time_now(),
            updated_at=time_now(),
        )

    def _adapt_transitions(
        self, ticket_id: str, history: List[ShortcutHistoryEntry]
    ) -> List[TicketStateTransition]:
        transitions: List[TicketStateTransition] = []
        seen = set()

        for entry in history:
            if not entry.changed_at:
                continue

            state_refs = self._index_state_references(entry)

            for action in entry.actions:
                change = self._extract_state_change(action)
                if not change:
                    continue
                from_id, to_id = change

                key = (entry.changed_at, to_id)
                if key in seen:
                    continue
                seen.add(key)

                from_state = self._resolve_state(from_id, state_refs)
                to_state = self._resolve_state(to_id, state_refs)

                transitions.append(
                    TicketStateTransition(
                        id=uuid4_str(),
                        ticket_id=ticket_id,
                        from_state=from_state[0],
                        from_state_type=from_state[1],
                        to_state=to_state[0],
                        to_state_type=to_state[1],
                        actor=entry.actor_name,
                        changed_at=entry.changed_at,
                    )
                )

        transitions.sort(key=lambda t: t.changed_at)
        return transitions

    def _adapt_git_links(
        self, ticket_id: str, history: List[ShortcutHistoryEntry]
    ) -> List[TicketPullRequestMap]:
        """Collect branch and PR events, pairing them per repository.

        A branch push and the PR it produces arrive as separate history
        entries, so the branch name is carried forward to whichever PR appears
        for the same repo.
        """
        branches_by_repo: Dict[str, str] = {}
        links: Dict[Tuple[str, int], TicketPullRequestMap] = {}

        for entry in history:
            for action in entry.actions:
                git_link = self._extract_git_link(action)
                if not git_link or not git_link.repo_name:
                    continue

                if git_link.branch_name:
                    branches_by_repo[git_link.repo_name] = git_link.branch_name

                if git_link.pr_number is None:
                    continue

                links[(git_link.repo_name, git_link.pr_number)] = TicketPullRequestMap(
                    ticket_id=ticket_id,
                    repo_name=git_link.repo_name,
                    pr_number=git_link.pr_number,
                    branch_name=branches_by_repo.get(git_link.repo_name),
                )

        return list(links.values())

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_state_change(action: Dict) -> Optional[Tuple[Optional[str], str]]:
        """Return (from_state_id, to_state_id) for a state change, else None.

        Two shapes carry a state: the initial `create`, which has a flat
        `workflow_state_id`, and later `update`s, which nest it under
        `changes`.
        """
        if action.get("action") == "create" and action.get("workflow_state_id"):
            return None, str(action["workflow_state_id"])

        changes = action.get("changes") or {}
        state_change = changes.get("workflow_state_id")
        if not isinstance(state_change, dict) or state_change.get("new") is None:
            return None
        old = state_change.get("old")
        return (str(old) if old is not None else None, str(state_change["new"]))

    @staticmethod
    def _extract_git_link(action: Dict) -> Optional[ShortcutGitLink]:
        entity_type = action.get("entity_type")
        if entity_type not in ("branch", "pull-request"):
            return None

        repo_name = ShortcutETLHandler._repo_name_from_url(action.get("url"))

        if entity_type == "branch":
            return ShortcutGitLink(
                repo_name=repo_name,
                pr_number=None,
                branch_name=action.get("name"),
            )

        number = action.get("number")
        return ShortcutGitLink(
            repo_name=repo_name,
            pr_number=int(number) if number is not None else None,
            branch_name=None,
        )

    @staticmethod
    def _repo_name_from_url(url: Optional[str]) -> Optional[str]:
        """Pull the repository name out of a GitHub URL.

        `https://github.com/zincwork/mvp-api/pull/6906` -> `mvp-api`, matching
        Middleware's OrgRepo.name rather than the owner/name pair.
        """
        if not url:
            return None
        parts = [part for part in url.split("/") if part]
        try:
            host_index = next(
                i for i, part in enumerate(parts) if "github.com" in part
            )
        except StopIteration:
            return None
        if len(parts) < host_index + 3:
            return None
        return parts[host_index + 2]

    def _index_state_references(
        self, entry: ShortcutHistoryEntry
    ) -> Dict[str, Tuple[str, str]]:
        """State id -> (name, type) from this entry's own references.

        History carries the state names inline, which means transitions
        resolve correctly even for states that have since been renamed or
        deleted from the workflow.
        """
        indexed: Dict[str, Tuple[str, str]] = {}
        for reference in entry.references:
            if reference.get("entity_type") != "workflow-state":
                continue
            indexed[str(reference.get("id"))] = (
                reference.get("name"),
                TicketStateType.from_provider(reference.get("type")).value,
            )
        return indexed

    def _resolve_state(
        self, state_id: Optional[str], state_refs: Dict[str, Tuple[str, str]]
    ) -> Tuple[Optional[str], Optional[str]]:
        if not state_id:
            return None, None
        if state_id in state_refs:
            return state_refs[state_id]
        state = self._workflow_states.get(state_id)
        if state:
            return state.name, TicketStateType.from_provider(state.state_type).value
        return None, TicketStateType.UNKNOWN.value

    @staticmethod
    def _is_cancelled(state_name: Optional[str]) -> bool:
        if not state_name:
            return False
        return state_name.strip().lower() in CANCELLED_STATE_NAMES

    @staticmethod
    def _extract_priority(story: ShortcutStory) -> Optional[str]:
        """Priority is a custom field, so it has to be read out of the raw story."""
        for value in story.raw.get("custom_fields") or []:
            if (value.get("field_name") or "").lower() == "priority":
                return value.get("value") or value.get("value_name")
        return None

    @staticmethod
    def _build_meta(story: ShortcutStory, state: Optional[ShortcutWorkflowState]) -> Dict:
        return {
            "provider": TicketProviders.SHORTCUT.value,
            "story_id": story.id,
            "workflow_state_id": story.workflow_state_id,
            "workflow_name": state.workflow_name if state else None,
            "state_name": state.name if state else None,
            "owner_ids": story.raw.get("owner_ids") or [],
            "sub_task_ids": story.raw.get("sub_task_ids") or [],
        }

    def _get_new_bookmark(
        self, stories: List[ShortcutStory], fallback: datetime
    ) -> datetime:
        """The newest `updated_at` seen, minus a small overlap.

        The overlap exists because Shortcut's `updated:` search granularity is
        a whole day; rewinding slightly is cheaper than missing a change.
        """
        timestamps = [story.updated_at for story in stories if story.updated_at]
        if not timestamps:
            return fallback
        return max(timestamps) - timedelta(hours=1)


def get_shortcut_etl_handler(org_id: str) -> ShortcutETLHandler:
    from mhq.service.tickets.integration import get_tickets_integration_service

    token = get_tickets_integration_service().get_access_token(
        org_id, TicketProviders.SHORTCUT.value
    )
    if not token:
        LOG.error(
            f"No Shortcut token found for org {org_id}. Run setup_shortcut.py first."
        )

    return ShortcutETLHandler(ShortcutApiService(token), TicketsRepoService())
