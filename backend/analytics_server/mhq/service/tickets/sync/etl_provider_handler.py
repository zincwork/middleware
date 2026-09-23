from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Tuple

from mhq.service.tickets.models import RawTicketBundle


class TicketProviderETLHandler(ABC):
    """Deliberately narrow, mirroring WorkflowProviderETLHandler.

    Two methods keeps the cost of a second provider (Jira, Linear) low, and
    keeps provider-specific parsing out of the orchestrator.
    """

    @abstractmethod
    def check_pat_validity(self) -> bool:
        """Raise if the stored token cannot be used."""
        pass

    @abstractmethod
    def get_tickets(
        self, org_id: str, bookmark: datetime
    ) -> Tuple[List[RawTicketBundle], datetime]:
        """Tickets updated since the bookmark, with the new bookmark.

        Each bundle carries the ticket plus its state transitions and git
        links, because for every provider those come from the same fetch.
        """
        pass
