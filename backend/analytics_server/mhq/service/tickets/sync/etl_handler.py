"""
Ticket sync orchestrator.

Mirrors the code and workflow sync handlers: resolve the org's ticket
providers, read the watermark, delegate to the provider handler, persist, then
move the watermark.

Non-destructive by construction:
  * if no ticket integration exists it returns immediately, so an org that has
    not set Shortcut up is unaffected;
  * every write touches only ticket-layer tables;
  * `trigger_data_sync` already wraps each step in try/except and continues,
    so a failure here cannot stop the code, workflow or incident syncs.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from mhq.service.tickets.integration import get_tickets_integration_service
from mhq.service.tickets.models import RawTicketBundle
from mhq.service.tickets.sync.etl_tickets_factory import TicketsETLFactory
from mhq.store.repos.tickets import TicketsRepoService
from mhq.utils.log import LOG
from mhq.utils.time import time_now

DEFAULT_SYNC_DAYS = 120


class TicketsETLHandler:
    def __init__(
        self,
        tickets_repo_service: TicketsRepoService,
        etl_factory: TicketsETLFactory,
    ):
        self.tickets_repo_service = tickets_repo_service
        self.etl_factory = etl_factory

    def sync_org_tickets(self, org_id: str) -> None:
        providers = get_tickets_integration_service().get_org_providers(org_id)
        if not providers:
            LOG.info(
                f"[Tickets Sync] No ticket integration for org {org_id}, skipping"
            )
            return

        for provider in providers:
            try:
                self._sync_provider(org_id, provider)
            except Exception as e:
                LOG.error(
                    f"[Tickets Sync] Error syncing {provider} for org {org_id}: {str(e)}"
                )
                continue

    def _sync_provider(self, org_id: str, provider: str) -> None:
        handler = self.etl_factory(provider)

        if not handler.check_pat_validity():
            LOG.error(f"[Tickets Sync] Invalid token for {provider}, org {org_id}")
            return

        bookmark = self._get_bookmark(org_id, provider)
        bundles, new_bookmark = handler.get_tickets(org_id, bookmark)

        if not bundles:
            LOG.info(f"[Tickets Sync] Nothing new for {provider}, org {org_id}")
            return

        self._persist(bundles)

        resolved = self.tickets_repo_service.resolve_pull_request_ids(org_id)
        LOG.info(
            f"[Tickets Sync] {len(bundles)} ticket(s) saved for {provider}, "
            f"{resolved} pull request link(s) resolved"
        )

        self.tickets_repo_service.update_bookmark(org_id, provider, new_bookmark)

    def _persist(self, bundles: List[RawTicketBundle]) -> None:
        # Tickets first: transitions and PR links reference them.
        self.tickets_repo_service.save_tickets(
            [bundle.ticket for bundle in bundles]
        )

        for bundle in bundles:
            ticket_id = str(bundle.ticket.id)
            self.tickets_repo_service.replace_transitions_for_ticket(
                ticket_id, bundle.transitions
            )
            self.tickets_repo_service.save_pull_request_links(
                bundle.pull_request_links
            )

    def _get_bookmark(self, org_id: str, provider: str) -> Optional[datetime]:
        row = self.tickets_repo_service.get_bookmark(org_id, provider)
        if not row or not row.bookmark:
            return time_now() - timedelta(days=DEFAULT_SYNC_DAYS)
        try:
            return datetime.fromisoformat(row.bookmark)
        except ValueError:
            LOG.warning(
                f"[Tickets Sync] Unparseable bookmark '{row.bookmark}' for "
                f"{provider}, falling back to {DEFAULT_SYNC_DAYS} days"
            )
            return time_now() - timedelta(days=DEFAULT_SYNC_DAYS)


def sync_org_tickets(org_id: str) -> None:
    TicketsETLHandler(
        TicketsRepoService(), TicketsETLFactory(org_id)
    ).sync_org_tickets(org_id)
