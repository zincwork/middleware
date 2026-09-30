"""
Tests for the ticket backfill: the sync-days setting and the bookmark rewind.

Both of these exist to fix the same gap. Layer 1 hardcoded a 120-day backfill
and gave tickets their own watermark table, which meant there was no supported
way to load older history: widening the window had no effect once a bookmark
existed, and the shared `bookmark/reset` endpoint did not touch the ticket
watermark.

The tests below pin down the two behaviours that gap turned on — that the
window comes from the org setting, and that a reset reaches every ticket
provider — plus the thing that is easy to get wrong when fixing it: the
window must still be ignored once a bookmark exists, because that is what
makes the sync incremental rather than a full re-read every night.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytz

from mhq.service.tickets.sync.etl_shortcut_handler import (
    DEFAULT_BACKFILL_DAYS,
    ShortcutETLHandler,
    get_backfill_days,
)

ORG_ID = "9c8f2a24-1c1f-4b7f-9a2e-4a26d1c1f0aa"


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(pytz.UTC)


# --------------------------------------------------------------------------
# get_backfill_days
# --------------------------------------------------------------------------


def _with_setting(days):
    """Patch the settings service to report `days`, or None for no setting."""
    service = MagicMock()
    if days is None:
        service.get_settings.return_value = None
    else:
        service.get_settings.return_value = SimpleNamespace(
            specific_settings=SimpleNamespace(default_sync_days=days)
        )
    return patch(
        "mhq.service.settings.configuration_settings.get_settings_service",
        return_value=service,
    )


def test_backfill_window_comes_from_the_org_setting():
    with _with_setting(366):
        assert get_backfill_days(ORG_ID) == 366


def test_falls_back_when_no_setting_has_been_saved():
    """A fresh org must still get a usable window, not zero days."""
    with _with_setting(None):
        assert get_backfill_days(ORG_ID) == DEFAULT_BACKFILL_DAYS


def test_falls_back_on_a_nonsense_setting():
    """0 or a negative would make `now - 0 days` and sync nothing at all."""
    for value in (0, -1, None):
        with _with_setting(value):
            assert get_backfill_days(ORG_ID) == DEFAULT_BACKFILL_DAYS


def test_a_settings_failure_does_not_stop_the_sync():
    """Reading a setting must never be the reason history goes missing."""
    service = MagicMock()
    service.get_settings.side_effect = RuntimeError("database is away")
    with patch(
        "mhq.service.settings.configuration_settings.get_settings_service",
        return_value=service,
    ):
        assert get_backfill_days(ORG_ID) == DEFAULT_BACKFILL_DAYS


def test_the_setting_is_read_not_written():
    """`get_settings`, never `get_or_set_default_settings`.

    A ticket sync that writes a settings row as a side effect would be
    creating org configuration from a background job, which is not its place.
    """
    service = MagicMock()
    service.get_settings.return_value = None
    with patch(
        "mhq.service.settings.configuration_settings.get_settings_service",
        return_value=service,
    ):
        get_backfill_days(ORG_ID)

    service.get_settings.assert_called_once()
    service.get_or_set_default_settings.assert_not_called()
    service.save_settings.assert_not_called()


# --------------------------------------------------------------------------
# The window only applies without a bookmark
# --------------------------------------------------------------------------


def _handler(backfill_days):
    api = MagicMock()
    api.get_workflow_states.return_value = {}
    api.search_stories.return_value = []
    return ShortcutETLHandler(api, MagicMock(), backfill_days=backfill_days), api


def test_the_window_sets_the_query_when_there_is_no_bookmark():
    handler, api = _handler(365)
    handler.get_tickets(ORG_ID, None)

    query = api.search_stories.call_args[0][0]
    expected = (datetime.now(pytz.UTC) - timedelta(days=365)).strftime("%Y-%m-%d")
    assert query == f"updated:{expected}..*"


def test_a_bookmark_beats_the_window():
    """This is what keeps the nightly sync incremental.

    If widening the window also widened every subsequent sync, each night
    would re-read a year of stories and re-fetch a year of history — about
    3,300 Shortcut calls against a 200-per-minute limit, every night, for no
    new data.
    """
    handler, api = _handler(365)
    handler.get_tickets(ORG_ID, dt("2026-09-01T00:00:00Z"))

    assert api.search_stories.call_args[0][0] == "updated:2026-09-01..*"


def test_widening_the_window_alone_changes_nothing_once_synced():
    """The trap this layer exists to remove.

    Raising the setting looks like it should load history. It does not, until
    the bookmark is rewound — which is why the reset endpoint had to learn
    about tickets.
    """
    narrow, narrow_api = _handler(30)
    wide, wide_api = _handler(365)

    bookmark = dt("2026-09-01T00:00:00Z")
    narrow.get_tickets(ORG_ID, bookmark)
    wide.get_tickets(ORG_ID, bookmark)

    assert (
        narrow_api.search_stories.call_args[0][0]
        == wide_api.search_stories.call_args[0][0]
    )


# --------------------------------------------------------------------------
# reset_org_ticket_bookmarks
# --------------------------------------------------------------------------


def test_reset_rewinds_every_ticket_provider():
    from mhq.service.tickets.bookmark import reset_org_ticket_bookmarks
    from mhq.store.models.tickets import TicketProviders

    repo = MagicMock()
    with patch(
        "mhq.service.tickets.bookmark.TicketsRepoService", return_value=repo
    ):
        rewound = reset_org_ticket_bookmarks(ORG_ID, dt("2024-09-28T00:00:00Z"))

    assert rewound == [p.value for p in TicketProviders]
    assert repo.update_bookmark.call_count == len(list(TicketProviders))
    args = repo.update_bookmark.call_args[0]
    assert args[0] == ORG_ID
    assert args[2] == dt("2024-09-28T00:00:00Z")


def test_reset_creates_the_row_when_the_org_has_never_synced():
    """"Rewind to 2024" has to mean that even before a first sync.

    update_bookmark inserts when absent, so the requested date is honoured
    rather than being overridden by the backfill window.
    """
    from mhq.service.tickets.bookmark import reset_org_ticket_bookmarks

    repo = MagicMock()
    repo.get_bookmark.return_value = None
    with patch(
        "mhq.service.tickets.bookmark.TicketsRepoService", return_value=repo
    ):
        rewound = reset_org_ticket_bookmarks(ORG_ID, dt("2024-09-28T00:00:00Z"))

    assert rewound
    assert repo.update_bookmark.called


def test_reset_reports_what_it_rewound_rather_than_assuming():
    """The endpoint returns this list, so a silent no-op is impossible."""
    from mhq.service.tickets.bookmark import reset_org_ticket_bookmarks

    with patch(
        "mhq.service.tickets.bookmark.TicketsRepoService", return_value=MagicMock()
    ):
        rewound = reset_org_ticket_bookmarks(ORG_ID, dt("2024-09-28T00:00:00Z"))

    assert isinstance(rewound, list)
    assert "shortcut" in rewound
