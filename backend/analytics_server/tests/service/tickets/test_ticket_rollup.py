"""
Tests for the manager roll-up: which figures may be combined, and how.

This file exists to make one mistake impossible to reintroduce quietly.

A roll-up across teams looks like it could be assembled from the per-team
figures the comparison view already has in hand. For counts that is true. For
averages and percentiles it is not, and the error is not small: a manager
holding a three-ticket team and a thirty-ticket team who averages the two
medians gets a number that is the median of nothing at all.

So the web-server asks the analytics server for the union of the teams'
Shortcut ids in a single query, and the tests below assert that the two
methods give materially different answers. If someone later "simplifies" the
roll-up into a mean of the rows above it, these fail.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytz

from mhq.service.tickets.analytics import TicketAnalyticsService
from mhq.store.models.tickets.filter import TicketFilter
from mhq.utils.time import Interval

ORG_ID = "9c8f2a24-1c1f-4b7f-9a2e-4a26d1c1f0aa"

# Two real Zinc squads, both mapped to Shortcut.
BLISS = "67a23586-5b81-45d2-ae4b-d1ee01c61b50"
INTEGRATIONS = "6a2c09a9-a226-41e9-8dd1-58224c66b28a"

DAY = 86400


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(pytz.UTC)


INTERVAL = Interval(dt("2026-06-24T00:00:00Z"), dt("2026-09-22T00:00:00Z"))
AS_OF = dt("2026-09-22T00:00:00Z")


def ticket(key, team, cycle_days, ticket_type="feature", started="2026-07-01T09:00:00Z"):
    """A ticket with a known cycle time and no state history.

    No transitions, deliberately: cycle time comes from the provider's own
    started_at and completed_at, so the arithmetic under test is not mixed up
    with the span-building logic tested elsewhere.
    """
    started_at = dt(started)
    return SimpleNamespace(
        id="{}-0000-0000-0000-000000000000".format(key),
        org_id=ORG_ID,
        provider="shortcut",
        key=key,
        title=key,
        ticket_type=ticket_type,
        provider_team_id=team,
        provider_epic_id=None,
        provider_iteration_id=None,
        workflow_id="500000005",
        current_state="Done",
        current_state_type="done",
        estimate=None,
        is_subtask=False,
        is_complete=True,
        is_cancelled=False,
        provider_created_at=started_at,
        started_at=started_at,
        completed_at=started_at.fromtimestamp(
            started_at.timestamp() + cycle_days * DAY, tz=pytz.UTC
        ),
    )


# One slow ticket on Integration, nine fast ones on Bliss. Lopsided on
# purpose: this is the shape that makes averaging averages wrong, and it is
# not a contrived shape — a small team working a long-running piece of
# integration work next to a squad shipping small changes is ordinary.
SLOW = [ticket("11111111", INTEGRATIONS, cycle_days=100)]
FAST = [
    ticket("2222222{}".format(i), BLISS, cycle_days=1, ticket_type="bug" if i < 3 else "feature")
    for i in range(9)
]


def service_for(tickets):
    repo = MagicMock()
    repo.get_tickets_completed_in_interval.return_value = tickets
    repo.get_tickets_active_in_interval.return_value = tickets
    repo.get_open_tickets.return_value = []
    repo.get_transitions_for_tickets.return_value = {}
    repo.get_pull_request_links_for_tickets.return_value = {}
    repo.get_tickets_for_epics.return_value = []
    return TicketAnalyticsService(repo)


def metrics_for(tickets, provider_team_ids):
    return service_for(tickets).get_flow_metrics(
        ORG_ID,
        INTERVAL,
        TicketFilter(provider_team_ids=provider_team_ids),
        as_of=AS_OF,
    )


# --------------------------------------------------------------------------
# The one that matters
# --------------------------------------------------------------------------


def test_averaging_the_per_team_medians_gives_the_wrong_answer():
    """The roll-up must come from the tickets, not from the rows above it.

    Integration: one ticket, 100 days.
    Bliss:       nine tickets, 1 day each.

    Averaging the two medians gives 50.5 days, which describes no ticket that
    exists. The median of the ten actual tickets is 1 day. Anyone reading
    "50.5 days" would conclude Lucila's area has a serious flow problem; the
    truth is one long-running piece of work and nine ordinary ones.
    """
    integration = metrics_for(SLOW, [INTEGRATIONS])
    bliss = metrics_for(FAST, [BLISS])
    combined = metrics_for(SLOW + FAST, [BLISS, INTEGRATIONS])

    assert integration.cycle_time.p50 == 100 * DAY
    assert bliss.cycle_time.p50 == 1 * DAY

    naive_mean = (integration.cycle_time.p50 + bliss.cycle_time.p50) / 2
    assert naive_mean == 50.5 * DAY

    # The truth, from the ten tickets.
    assert combined.cycle_time.p50 == 1 * DAY
    assert combined.cycle_time.count == 10

    # Fifty times out. Not a rounding difference — a different conclusion.
    assert naive_mean > combined.cycle_time.p50 * 50


def test_the_tail_survives_the_union():
    """Combining must not hide the slow ticket either.

    The p95 is where the 100-day ticket shows up, which is the whole reason
    the roll-up reports percentiles rather than a mean.
    """
    combined = metrics_for(SLOW + FAST, [BLISS, INTEGRATIONS])

    assert combined.cycle_time.p50 == 1 * DAY
    assert combined.cycle_time.maximum == 100 * DAY
    assert combined.cycle_time.p95 > 10 * DAY


def test_counts_are_additive_and_may_be_summed():
    """Not everything needs the union — counts do add.

    Worth pinning down, because the distinction is the point: sum the
    countable things, re-query the computed ones.
    """
    integration = metrics_for(SLOW, [INTEGRATIONS])
    bliss = metrics_for(FAST, [BLISS])
    combined = metrics_for(SLOW + FAST, [BLISS, INTEGRATIONS])

    assert combined.throughput == integration.throughput + bliss.throughput == 10
    assert (
        combined.completed_by_type["bug"]
        == integration.completed_by_type.get("bug", 0)
        + bliss.completed_by_type["bug"]
    )


def test_the_bug_ratio_is_not_additive_either():
    """A ratio of sums, not a sum of ratios.

    Integration: 0 of 1 bugs -> 0%.
    Bliss:       3 of 9 bugs -> 33.3%.

    Averaging those two gives about 16.6%. The real figure is 3 of 10 -> 30%,
    because the denominators differ. Halving the bug ratio of a manager's
    area is the kind of error that gets noticed a quarter later.
    """
    integration = metrics_for(SLOW, [INTEGRATIONS])
    bliss = metrics_for(FAST, [BLISS])
    combined = metrics_for(SLOW + FAST, [BLISS, INTEGRATIONS])

    assert integration.bug_ratio == 0.0
    assert bliss.bug_ratio == 33.3
    assert combined.bug_ratio == 30.0

    naive_mean = (integration.bug_ratio + bliss.bug_ratio) / 2
    # Roughly half the truth. Asserted as a relationship rather than an exact
    # figure, because the exact value of the WRONG method is not worth
    # pinning down — only that it is wrong by a lot.
    assert naive_mean < combined.bug_ratio * 0.6


def test_the_union_filter_carries_every_team_id():
    """One query, both teams — not one query per team merged afterwards."""
    svc = service_for(SLOW + FAST)
    svc.get_flow_metrics(
        ORG_ID,
        INTERVAL,
        TicketFilter(provider_team_ids=[BLISS, INTEGRATIONS]),
        as_of=AS_OF,
    )

    used = svc._repo.get_tickets_completed_in_interval.call_args[0][2]
    assert sorted(used.provider_team_ids) == sorted([BLISS, INTEGRATIONS])
    assert svc._repo.get_tickets_completed_in_interval.call_count == 1


def test_a_single_team_rollup_equals_that_team():
    """A manager holding one team must see exactly that team's numbers.

    Four of Zinc's five managers hold one team each, so this is the common
    case, and it has to be identical rather than merely close.
    """
    alone = metrics_for(FAST, [BLISS])
    as_rollup = metrics_for(FAST, [BLISS])

    assert alone.cycle_time == as_rollup.cycle_time
    assert alone.throughput == as_rollup.throughput
    assert alone.bug_ratio == as_rollup.bug_ratio
