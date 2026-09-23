from dataclasses import dataclass, field
from typing import List

from mhq.store.models.tickets import (
    Ticket,
    TicketPullRequestMap,
    TicketStateTransition,
)


@dataclass
class RawTicketBundle:
    """One ticket plus everything derived from the same provider fetch.

    Grouped rather than returned as three parallel lists so a partial failure
    on one ticket cannot desynchronise the three.
    """

    ticket: Ticket
    transitions: List[TicketStateTransition] = field(default_factory=list)
    pull_request_links: List[TicketPullRequestMap] = field(default_factory=list)
