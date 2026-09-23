def sync_org_tickets(org_id: str) -> None:
    """Run the ticket sync for an org.

    The real import lives INSIDE this function, deliberately.

    `mhq/service/sync_data.py` imports this name at module load. That module
    is imported by `mhq/api/sync.py`, which is imported by `sync_app.py` — so
    an ImportError anywhere in the ticket package would stop gunicorn from
    starting the sync server at all, and the try/except inside
    `trigger_data_sync` could not catch it because it would never get to run.

    Importing lazily moves any such failure to call time, inside that
    try/except, where it is logged and skipped like any other sync error. That
    is the isolation this layer promises: a broken ticket sync must never take
    down the code, workflow, merge-to-deploy or incident syncs.
    """
    from mhq.service.tickets.sync.etl_handler import (
        sync_org_tickets as _sync_org_tickets,
    )

    return _sync_org_tickets(org_id)
