from dataclasses import dataclass
from typing import List

from mhq.store.models.tickets.tickets import Ticket


@dataclass
class TicketFilter:
    """Scope for the ticket flow metrics.

    Mirrors PRFilter's shape — a dataclass whose `filter_query` property
    returns a list of SQLAlchemy conditions — so it composes the same way and
    reads the same way to anyone who already knows that code.

    `provider_team_ids` holds the Shortcut team(s) mapped to the selected
    GitHub team. The web-server resolves that from github_teams.json, so this
    layer never needs to know GitHub exists.
    """

    provider_team_ids: List[str] = None
    ticket_types: List[str] = None
    epic_ids: List[str] = None
    # Zinc runs four Shortcut workflows and only three of them are delivery
    # work: "Mid-Longterm themes" (Captured -> Investigation -> Aligned) holds
    # theme records, not stories, and counting those as throughput would be
    # wrong. Left unset by default — the response reports the per-workflow
    # split so the distortion is visible before anyone filters it out.
    workflow_ids: List[str] = None
    # Kept for the day Zinc starts using Shortcut iterations. Every story
    # currently has iteration_id null, so this filters nothing today.
    iteration_ids: List[str] = None

    # "Cancelled " is a done-type state in every Zinc workflow, so it would
    # otherwise count as delivered work. Excluded by default, deliberately.
    exclude_cancelled: bool = True
    # The parent story is the unit of work, so sub-tasks are excluded by
    # default to stop a ten-subtask story counting eleven times.
    include_subtasks: bool = False

    @property
    def filter_query(self) -> List:
        conditions = []

        if self.provider_team_ids:
            conditions.append(Ticket.provider_team_id.in_(self.provider_team_ids))
        if self.ticket_types:
            conditions.append(Ticket.ticket_type.in_(self.ticket_types))
        if self.epic_ids:
            conditions.append(Ticket.provider_epic_id.in_(self.epic_ids))
        if self.workflow_ids:
            conditions.append(Ticket.workflow_id.in_(self.workflow_ids))
        if self.iteration_ids:
            conditions.append(Ticket.provider_iteration_id.in_(self.iteration_ids))
        if self.exclude_cancelled:
            conditions.append(Ticket.is_cancelled.is_(False))
        if not self.include_subtasks:
            conditions.append(Ticket.is_subtask.is_(False))

        return conditions

    @property
    def is_scoped_to_team(self) -> bool:
        return bool(self.provider_team_ids)
