"""
Ticket-provider integration lookup.

Reads the stored token using only public helpers — `get_org_integrations_for_names`
takes plain strings and `get_crypto_service` is a public factory — so this
needs no change to `UserIdentityProvider` or any other existing module. The
ticket layer's only edit to pre-existing code is two lines in sync_data.py.
"""

from typing import List, Optional

from mhq.store.models.tickets import TicketProviders
from mhq.store.repos.core import CoreRepoService
from mhq.utils.cryptography import get_crypto_service
from mhq.utils.log import LOG

TICKET_INTEGRATION_BUCKET = [
    TicketProviders.SHORTCUT.value,
]


class TicketsIntegrationService:
    def __init__(self, core_repo_service: CoreRepoService):
        self._core_repo_service = core_repo_service
        self._crypto = get_crypto_service()

    def get_org_providers(self, org_id: str) -> List[str]:
        integrations = self._core_repo_service.get_org_integrations_for_names(
            org_id, TICKET_INTEGRATION_BUCKET
        )
        if not integrations:
            return []
        return [integration.name for integration in integrations]

    def get_access_token(self, org_id: str, provider: str) -> Optional[str]:
        integrations = self._core_repo_service.get_org_integrations_for_names(
            org_id, [provider]
        )
        if not integrations:
            LOG.warning(
                f"No '{provider}' integration found for org {org_id}"
            )
            return None

        chunks = integrations[0].access_token_enc_chunks
        if not chunks:
            LOG.warning(
                f"Integration '{provider}' for org {org_id} has no stored token"
            )
            return None

        return self._crypto.decrypt_chunks(chunks)


def get_tickets_integration_service() -> TicketsIntegrationService:
    return TicketsIntegrationService(CoreRepoService())
