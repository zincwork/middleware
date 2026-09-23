"""Read-side queries for the ticket flow metrics.

Deliberately a separate service from TicketsRepoService rather than more
methods on it. TicketsRepoService is the write path the sync depends on, and
Layer 1 is installed and working; keeping the read path in its own file means
Layer 2 adds nothing to a file the sync imports, so a mistake here cannot
break ingestion.

Every method is read-only. Nothing in this file commits, merges or deletes.
"""

from datetime import datetime
from typing import Dict, List

from sqlalchemy import and_, or_

from mhq.store import db, rollback_on_exc
from mhq.store.models.tickets.filter import TicketFilter
from mhq.store.models.tickets.tickets import (
    Ticket,
    TicketPullRequestMap,
    TicketStateTransition,
)


class TicketAnalyticsRepoService:
    def __init__(self):
        self._db = db

    def _base_query(self, org_id: str, provider: str, ticket_filter: TicketFilter):
        return self._db.session.query(Ticket).filter(
            and_(
                Ticket.org_id == org_id,
                Ticket.provider == provider,
                *(ticket_filter.filter_query if ticket_filter else []),
            )
        )

    @rollback_on_exc
    def get_tickets_completed_in_interval(
        self,
        org_id: str,
        provider: str,
        ticket_filter: TicketFilter,
        from_time: datetime,
        to_time: datetime,
    ) -> List[Ticket]:
        """Tickets that reached a done state inside the window.

        Throughput, cycle time and bug ratio are all measured on completion
        date, which is the convention Middleware already uses for deployments
        and incidents — so a week's number never changes retrospectively.
        """
        return (
            self._base_query(org_id, provider, ticket_filter)
            .filter(
                and_(
                    Ticket.completed_at.isnot(None),
                    Ticket.completed_at >= from_time,
                    Ticket.completed_at <= to_time,
                )
            )
            .order_by(Ticket.completed_at.asc())
            .all()
        )

    @rollback_on_exc
    def get_tickets_active_in_interval(
        self,
        org_id: str,
        provider: str,
        ticket_filter: TicketFilter,
        from_time: datetime,
        to_time: datetime,
    ) -> List[Ticket]:
        """Tickets that were open at any point in the window.

        Anything created before the window closed and either still open or
        completed after the window opened. This is the set the per-state
        durations are computed over: a story that sat in Review Requested for
        three weeks and is still sitting there has to be visible, and it would
        not be if only completed work were counted.
        """
        return (
            self._base_query(org_id, provider, ticket_filter)
            .filter(
                and_(
                    or_(
                        Ticket.provider_created_at.is_(None),
                        Ticket.provider_created_at <= to_time,
                    ),
                    or_(
                        Ticket.completed_at.is_(None),
                        Ticket.completed_at >= from_time,
                    ),
                )
            )
            # Ordered so two identical requests give identical responses.
            # Without it Postgres row order decides how ties are broken in
            # the state table and the epic list.
            .order_by(Ticket.provider_created_at.asc(), Ticket.key.asc())
            .all()
        )

    @rollback_on_exc
    def get_open_tickets(
        self, org_id: str, provider: str, ticket_filter: TicketFilter
    ) -> List[Ticket]:
        """Everything not yet complete, as of now.

        WIP and blocked counts are point-in-time by nature — there is no
        meaningful "WIP during August" — so these are read without an
        interval, and the response carries an `as_of` timestamp to say so.
        """
        return (
            self._base_query(org_id, provider, ticket_filter)
            .filter(Ticket.completed_at.is_(None))
            .order_by(Ticket.provider_created_at.asc(), Ticket.key.asc())
            .all()
        )

    @rollback_on_exc
    def get_transitions_for_tickets(
        self, ticket_ids: List[str]
    ) -> Dict[str, List[TicketStateTransition]]:
        """All state transitions for many tickets, in one query, grouped by id.

        One query rather than one per ticket: a 90-day window for a Zinc squad
        is a few hundred stories, and per-ticket queries would make the
        endpoint slow enough to be useless.
        """
        if not ticket_ids:
            return {}

        rows = (
            self._db.session.query(TicketStateTransition)
            .filter(TicketStateTransition.ticket_id.in_(ticket_ids))
            .order_by(
                TicketStateTransition.ticket_id,
                TicketStateTransition.changed_at.asc(),
                # Tied timestamps are real — one bulk action can move a story
                # twice. The id does not recover the true order but it makes
                # it the same on every request.
                TicketStateTransition.id.asc(),
            )
            .all()
        )

        grouped: Dict[str, List[TicketStateTransition]] = {}
        for row in rows:
            grouped.setdefault(str(row.ticket_id), []).append(row)
        return grouped

    @rollback_on_exc
    def get_pull_request_links_for_tickets(
        self, ticket_ids: List[str]
    ) -> Dict[str, List[TicketPullRequestMap]]:
        if not ticket_ids:
            return {}

        rows = (
            self._db.session.query(TicketPullRequestMap)
            .filter(TicketPullRequestMap.ticket_id.in_(ticket_ids))
            .all()
        )

        grouped: Dict[str, List[TicketPullRequestMap]] = {}
        for row in rows:
            grouped.setdefault(str(row.ticket_id), []).append(row)
        return grouped

    @rollback_on_exc
    def get_tickets_for_epics(
        self, org_id: str, provider: str, ticket_filter: TicketFilter
    ) -> List[Ticket]:
        """Every ticket belonging to an epic, regardless of date.

        Epic progress is a completeness question, not a rate question — "38 of
        52 done" means nothing if it only counts the stories that happen to
        fall in the selected window.
        """
        return (
            self._base_query(org_id, provider, ticket_filter)
            .filter(Ticket.provider_epic_id.isnot(None))
            .order_by(Ticket.provider_epic_id.asc(), Ticket.key.asc())
            .all()
        )


def get_ticket_analytics_repo_service() -> TicketAnalyticsRepoService:
    return TicketAnalyticsRepoService()
