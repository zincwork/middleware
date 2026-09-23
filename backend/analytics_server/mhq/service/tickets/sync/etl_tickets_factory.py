from mhq.service.tickets.sync.etl_provider_handler import TicketProviderETLHandler
from mhq.service.tickets.sync.etl_shortcut_handler import get_shortcut_etl_handler
from mhq.store.models.tickets import TicketProviders


class TicketsETLFactory:
    def __init__(self, org_id: str):
        self.org_id = org_id

    def __call__(self, provider: str) -> TicketProviderETLHandler:
        if provider == TicketProviders.SHORTCUT.value:
            return get_shortcut_etl_handler(self.org_id)
        raise NotImplementedError(f"Unknown ticket provider - {provider}")
