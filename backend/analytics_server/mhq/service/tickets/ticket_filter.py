"""Parse a ticket_filter off the wire into a TicketFilter.

Deliberately much thinner than the PR equivalent: PRFilter runs through a
processor chain because it also merges in team and org settings. Tickets have
no settings, so a chain would be an abstraction over one step.

Unknown keys are ignored rather than rejected, matching how the PR filter
behaves, so an older frontend cannot 400 a newer backend.
"""

from typing import Dict, List, Optional

from mhq.store.models.tickets.filter import TicketFilter


def _string_list(value) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return None
    cleaned = [str(item) for item in value if item not in (None, "")]
    return cleaned or None


def _boolean(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return default


def apply_ticket_filter(ticket_filter: Dict = None) -> TicketFilter:
    payload = ticket_filter or {}

    return TicketFilter(
        provider_team_ids=_string_list(payload.get("provider_team_ids")),
        ticket_types=_string_list(payload.get("ticket_types")),
        epic_ids=_string_list(payload.get("epic_ids")),
        workflow_ids=_string_list(payload.get("workflow_ids")),
        iteration_ids=_string_list(payload.get("iteration_ids")),
        exclude_cancelled=_boolean(payload.get("exclude_cancelled"), True),
        include_subtasks=_boolean(payload.get("include_subtasks"), False),
    )
