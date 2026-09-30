"""Rewinding the ticket sync watermark.

Layer 1 gave tickets their own `TicketsBookmark` table so that adding ticket
ingestion required no change to the shared BookmarkService. That was the right
call for shipping ingestion and the wrong one for backfilling: the existing
`PUT /orgs/<org_id>/bookmark/reset` rewound the repo, incident, workflow and
merge-to-deploy watermarks and silently left tickets exactly where they were,
so a request to load a year of history loaded a year of pull requests and no
extra tickets at all.

This closes that gap from the ticket side. The shared BookmarkService still
knows nothing about tickets; the API route calls in here instead. One
direction of dependency, and it is ours pointing at theirs rather than the
reverse.
"""

from datetime import datetime
from typing import List

from mhq.store.models.tickets import TicketProviders
from mhq.store.repos.tickets import TicketsRepoService
from mhq.utils.log import LOG


def reset_org_ticket_bookmarks(
    org_id: str, bookmark_timestamp: datetime
) -> List[str]:
    """Set the ticket watermark for every known provider to `bookmark_timestamp`.

    The next sync then re-reads everything the provider reports as updated
    since that moment. Nothing is deleted: tickets are upserted on their
    idempotency key, and each ticket's state transitions are replaced from the
    provider's own history, which is authoritative and immutable. So a rewind
    re-reads and refreshes rather than duplicating.

    The row is created when absent rather than skipped, because "rewind to
    2024-09-28" has to mean that even for an org whose first sync has not run
    — otherwise the backfill window would quietly override the date asked for.
    A row for a provider that is never configured is inert.

    Returns the providers rewound, so the caller can report what happened
    instead of assuming it worked.
    """
    repo = TicketsRepoService()
    rewound: List[str] = []

    for provider in TicketProviders:
        repo.update_bookmark(org_id, provider.value, bookmark_timestamp)
        rewound.append(provider.value)

    LOG.info(
        f"[Tickets] Rewound bookmark to {bookmark_timestamp.isoformat()} for "
        f"org {org_id}, providers: {', '.join(rewound) or 'none'}"
    )
    return rewound
