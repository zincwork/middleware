from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import and_

from mhq.store import db, rollback_on_exc
from mhq.store.models.code.pull_requests import PullRequest
from mhq.store.models.tickets import (
    Ticket,
    TicketPullRequestMap,
    TicketStateTransition,
    TicketsBookmark,
)
from mhq.utils.string import uuid4_str
from mhq.utils.time import time_now


class TicketsRepoService:
    def __init__(self):
        self._db = db

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------

    @rollback_on_exc
    def get_ticket_by_idempotency_key(
        self, org_id: str, provider: str, idempotency_key: str
    ) -> Optional[Ticket]:
        return (
            self._db.session.query(Ticket)
            .filter(
                and_(
                    Ticket.org_id == org_id,
                    Ticket.provider == provider,
                    Ticket.idempotency_key == idempotency_key,
                )
            )
            .one_or_none()
        )

    @rollback_on_exc
    def get_tickets_by_idempotency_keys(
        self, org_id: str, provider: str, keys: List[str]
    ) -> List[Ticket]:
        if not keys:
            return []
        return (
            self._db.session.query(Ticket)
            .filter(
                and_(
                    Ticket.org_id == org_id,
                    Ticket.provider == provider,
                    Ticket.idempotency_key.in_(keys),
                )
            )
            .all()
        )

    @rollback_on_exc
    def save_tickets(self, tickets: List[Ticket]) -> List[Ticket]:
        """Upsert by primary key.

        The handler resolves each ticket's id from the existing row where one
        exists, so merge() updates in place rather than inserting duplicates.
        """
        if not tickets:
            return []
        [self._db.session.merge(ticket) for ticket in tickets]
        self._db.session.commit()
        return tickets

    @rollback_on_exc
    def get_tickets_for_provider_teams(
        self,
        org_id: str,
        provider: str,
        provider_team_ids: List[str],
        include_subtasks: bool = False,
    ) -> List[Ticket]:
        query = self._db.session.query(Ticket).filter(
            and_(Ticket.org_id == org_id, Ticket.provider == provider)
        )
        if provider_team_ids:
            query = query.filter(Ticket.provider_team_id.in_(provider_team_ids))
        if not include_subtasks:
            # Parent is the unit of work, so sub-tasks are excluded by default
            # to stop a ten-subtask story counting eleven times.
            query = query.filter(Ticket.is_subtask.is_(False))
        return query.all()

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    @rollback_on_exc
    def get_transitions_for_ticket(self, ticket_id: str) -> List[TicketStateTransition]:
        return (
            self._db.session.query(TicketStateTransition)
            .filter(TicketStateTransition.ticket_id == ticket_id)
            .order_by(TicketStateTransition.changed_at.asc())
            .all()
        )

    @rollback_on_exc
    def replace_transitions_for_ticket(
        self, ticket_id: str, transitions: List[TicketStateTransition]
    ) -> None:
        """Delete then insert, scoped to one ticket.

        History is authoritative and immutable at the provider, so replacing
        is simpler and safer than diffing — and it only ever touches rows
        belonging to this one ticket.
        """
        self._db.session.query(TicketStateTransition).filter(
            TicketStateTransition.ticket_id == ticket_id
        ).delete(synchronize_session=False)

        for transition in transitions:
            self._db.session.add(transition)
        self._db.session.commit()

    # ------------------------------------------------------------------
    # Ticket <-> pull request
    # ------------------------------------------------------------------

    @rollback_on_exc
    def save_pull_request_links(self, links: List[TicketPullRequestMap]) -> None:
        if not links:
            return
        [self._db.session.merge(link) for link in links]
        self._db.session.commit()

    @rollback_on_exc
    def resolve_pull_request_ids(self, org_id: str) -> int:
        """Fill in pull_request_id where Middleware has the matching PR.

        Matched on (repo name, PR number) via the number Shortcut recorded.
        Runs after the code sync, so PRs that arrive later get picked up on a
        subsequent pass. Returns how many rows were resolved.
        """
        unresolved = (
            self._db.session.query(TicketPullRequestMap)
            .filter(TicketPullRequestMap.pull_request_id.is_(None))
            .all()
        )
        if not unresolved:
            return 0

        numbers = {str(link.pr_number) for link in unresolved}
        candidates = (
            self._db.session.query(PullRequest)
            .filter(PullRequest.number.in_(list(numbers)))
            .all()
        )
        by_number: Dict[str, List[PullRequest]] = {}
        for pr in candidates:
            by_number.setdefault(str(pr.number), []).append(pr)

        resolved = 0
        for link in unresolved:
            matches = by_number.get(str(link.pr_number)) or []
            if len(matches) == 1:
                link.pull_request_id = matches[0].id
                resolved += 1
            # More than one PR shares this number across repos. Left null
            # rather than guessed — a wrong join is worse than a missing one.

        if resolved:
            self._db.session.commit()
        return resolved

    # ------------------------------------------------------------------
    # Bookmark
    # ------------------------------------------------------------------

    @rollback_on_exc
    def get_bookmark(self, org_id: str, provider: str) -> Optional[TicketsBookmark]:
        return (
            self._db.session.query(TicketsBookmark)
            .filter(
                and_(
                    TicketsBookmark.org_id == org_id,
                    TicketsBookmark.provider == provider,
                )
            )
            .one_or_none()
        )

    @rollback_on_exc
    def update_bookmark(
        self, org_id: str, provider: str, bookmark: datetime
    ) -> TicketsBookmark:
        row = self.get_bookmark(org_id, provider)
        if not row:
            row = TicketsBookmark(
                id=uuid4_str(), org_id=org_id, provider=provider
            )
            self._db.session.add(row)
        row.bookmark = bookmark.isoformat()
        row.updated_at = time_now()
        self._db.session.commit()
        return row
