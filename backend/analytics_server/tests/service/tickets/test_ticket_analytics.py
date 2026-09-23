"""
Tests for the ticket flow metrics.

The first fixture is the real, unedited state history of Zinc story sc-8765
("Credit Check: PII in Rollbar log"): created in Backlog on 26 August, moved
to In Progress on 1 September, to Review Requested 56 minutes later, and to
Done the following afternoon. The expected numbers below were computed by
hand from those four timestamps, so a change in the algorithm has to justify
itself against a real story rather than against a number this file invented.

That story also shows why the metric is worth having: DORA sees a one-day
cycle, because the first commit landed on 1 September. The ticket sat in
Backlog for six days before that.

The remaining fixtures are synthetic, covering the cases sc-8765 does not:
re-entering a state, Blocked (typed `unstarted` in Zinc, so it is invisible
to any started-based count), a still-open final state, cancellation and
sub-tasks.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytz

from mhq.service.tickets.analytics import (
    BLOCKED_STATE_NAMES,
    TicketAnalyticsService,
    build_state_spans,
    clip_spans_to_window,
    summarise_durations,
    _percentile,
)
from mhq.service.tickets.ticket_filter import apply_ticket_filter
from mhq.store.models.tickets.filter import TicketFilter
from mhq.utils.time import Interval

ORG_ID = "9c8f2a24-1c1f-4b7f-9a2e-4a26d1c1f0aa"
SKIPPER = "677be3f3-b1d5-488b-b5ba-9d3ca7d5012b"


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(pytz.UTC)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def make_ticket(**overrides):
    """A ticket stub.

    SimpleNamespace rather than the SQLAlchemy model so these tests need no
    database and no app context — the analytics code only ever reads
    attributes.
    """
    defaults = dict(
        id="11111111-1111-1111-1111-111111111111",
        org_id=ORG_ID,
        provider="shortcut",
        key="sc-1",
        title="A story",
        ticket_type="feature",
        provider_team_id=SKIPPER,
        provider_epic_id=None,
        provider_iteration_id=None,
        workflow_id="500000005",
        current_state="Done",
        current_state_type="done",
        estimate=None,
        is_subtask=False,
        is_complete=True,
        is_cancelled=False,
        provider_created_at=None,
        started_at=None,
        completed_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_transition_seq = [0]


def transition(from_state, from_type, to_state, to_type, changed_at, id=None):
    _transition_seq[0] += 1
    return SimpleNamespace(
        id=id or "t-{:04d}".format(_transition_seq[0]),
        from_state=from_state,
        from_state_type=from_type,
        to_state=to_state,
        to_state_type=to_type,
        changed_at=dt(changed_at),
        actor="Emily Yoon",
    )


# sc-8765, exactly as Shortcut recorded it.
SC_8765_CREATED = "2026-08-26T14:19:45.320Z"
SC_8765_STARTED = "2026-09-01T14:55:46.242Z"
SC_8765_REVIEW = "2026-09-01T15:51:24.566Z"
SC_8765_DONE = "2026-09-02T15:57:59.937Z"

SC_8765 = make_ticket(
    id="8765ffff-0000-0000-0000-000000000000",
    key="sc-8765",
    title="Credit Check: PII in Rollbar log",
    ticket_type="bug",
    provider_created_at=dt(SC_8765_CREATED),
    started_at=dt(SC_8765_STARTED),
    completed_at=dt(SC_8765_DONE),
    current_state="Done",
    current_state_type="done",
)

SC_8765_HISTORY = [
    transition("Backlog", "backlog", "In Progress", "started", SC_8765_STARTED),
    transition("In Progress", "started", "Review Requested", "started", SC_8765_REVIEW),
    transition("Review Requested", "started", "Done", "done", SC_8765_DONE),
]

# Hand-computed from the four timestamps above. Seconds are truncated, not
# rounded, because StateSpan.seconds uses int().
BACKLOG_SECONDS = 520560  # 6 days, 36 min, 0.922 s
IN_PROGRESS_SECONDS = 3338  # 55 min, 38.324 s
REVIEW_SECONDS = 86795  # 1 day, 6 min, 35.371 s
CYCLE_SECONDS = 90133  # started -> completed
LEAD_SECONDS = 610694  # created -> completed


# --------------------------------------------------------------------------
# build_state_spans
# --------------------------------------------------------------------------


def test_real_story_produces_three_spans_not_four():
    spans = build_state_spans(SC_8765, SC_8765_HISTORY, dt("2026-09-22T00:00:00Z"))

    # Done is terminal. Were it given a span, this story would report three
    # weeks "in Done" and swamp every other state on the chart.
    assert [s.state for s in spans] == ["Backlog", "In Progress", "Review Requested"]


def test_real_story_span_durations_match_shortcut():
    spans = {s.state: s.seconds for s in build_state_spans(
        SC_8765, SC_8765_HISTORY, dt("2026-09-22T00:00:00Z")
    )}

    assert spans["Backlog"] == BACKLOG_SECONDS
    assert spans["In Progress"] == IN_PROGRESS_SECONDS
    assert spans["Review Requested"] == REVIEW_SECONDS


def test_backlog_span_starts_at_creation_not_at_first_transition():
    """The stay before the first recorded transition has to be included.

    Shortcut's history has no entry for "entered Backlog" — the story was
    created there. Without synthesising that first span from
    provider_created_at, this story reports zero backlog time, and six days
    of the seven-day lead time vanish.
    """
    spans = build_state_spans(SC_8765, SC_8765_HISTORY, dt("2026-09-22T00:00:00Z"))
    backlog = next(s for s in spans if s.state == "Backlog")

    assert backlog.entered_at == dt(SC_8765_CREATED)
    assert backlog.left_at == dt(SC_8765_STARTED)


def test_spans_sum_to_lead_time():
    """Consistency check: the parts add up to the whole.

    Within a few seconds — each span truncates its own fractional second, so
    three spans can lose up to three seconds against a single subtraction.
    """
    total = sum(
        s.seconds
        for s in build_state_spans(SC_8765, SC_8765_HISTORY, dt("2026-09-22T00:00:00Z"))
    )
    assert abs(total - LEAD_SECONDS) <= 3


def test_repeat_visits_are_separate_spans_and_sum():
    """A story that bounces back must report the full wait, not the last leg.

    This is the case that makes per-state metrics worth more than "time since
    review started": review took two days in total, across two attempts.
    """
    ticket = make_ticket(
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-05T10:00:00Z"),
    )
    history = [
        transition("To Do", "unstarted", "In Progress", "started", "2026-09-01T10:00:00Z"),
        transition("In Progress", "started", "Review Requested", "started", "2026-09-02T10:00:00Z"),
        # Sent back
        transition("Review Requested", "started", "In Progress", "started", "2026-09-03T10:00:00Z"),
        transition("In Progress", "started", "Review Requested", "started", "2026-09-04T10:00:00Z"),
        transition("Review Requested", "started", "Done", "done", "2026-09-05T10:00:00Z"),
    ]

    spans = build_state_spans(ticket, history, dt("2026-09-22T00:00:00Z"))
    review = [s for s in spans if s.state == "Review Requested"]

    assert len(review) == 2
    assert sum(s.seconds for s in review) == 2 * 86400


def test_open_final_state_accrues_to_now_and_is_flagged():
    ticket = make_ticket(
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=None,
        is_complete=False,
        current_state="Review Requested",
        current_state_type="started",
    )
    history = [
        transition("To Do", "unstarted", "In Progress", "started", "2026-09-01T10:00:00Z"),
        transition("In Progress", "started", "Review Requested", "started", "2026-09-02T10:00:00Z"),
    ]

    spans = build_state_spans(ticket, history, dt("2026-09-05T10:00:00Z"))
    review = next(s for s in spans if s.state == "Review Requested")

    assert review.is_open is True
    assert review.seconds == 3 * 86400


def test_no_transitions_yields_no_spans():
    ticket = make_ticket(provider_created_at=dt("2026-09-01T09:00:00Z"))
    assert build_state_spans(ticket, [], dt("2026-09-05T10:00:00Z")) == []


def test_transitions_are_sorted_before_spanning():
    """The repo orders by changed_at, but the algorithm must not rely on it.

    Out-of-order input previously produced negative durations, which
    max(0, ...) would have hidden rather than fixed.
    """
    ticket = make_ticket(provider_created_at=dt("2026-09-01T09:00:00Z"))
    history = [
        transition("In Progress", "started", "Review Requested", "started", "2026-09-03T10:00:00Z"),
        transition("To Do", "unstarted", "In Progress", "started", "2026-09-02T10:00:00Z"),
    ]

    spans = build_state_spans(ticket, history, dt("2026-09-05T10:00:00Z"))

    assert [s.state for s in spans] == ["To Do", "In Progress", "Review Requested"]
    assert all(s.seconds >= 0 for s in spans)


def test_blocked_is_a_real_state_in_zincs_workflow():
    """Zinc types Blocked as `unstarted`, so it is not "in progress".

    Three of Zinc's four workflows have a Blocked state and all three type it
    `unstarted`. Any WIP count based on started-type states therefore omits
    blocked work entirely, which is why blocked is reported separately.
    """
    assert "blocked" in BLOCKED_STATE_NAMES

    ticket = make_ticket(
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-04T10:00:00Z"),
    )
    history = [
        transition("To Do", "unstarted", "In Progress", "started", "2026-09-01T10:00:00Z"),
        transition("In Progress", "started", "Blocked", "unstarted", "2026-09-02T10:00:00Z"),
        transition("Blocked", "unstarted", "In Progress", "started", "2026-09-03T10:00:00Z"),
        transition("In Progress", "started", "Done", "done", "2026-09-04T10:00:00Z"),
    ]

    spans = {s.state: s.seconds for s in build_state_spans(
        ticket, history, dt("2026-09-22T00:00:00Z")
    )}
    assert spans["Blocked"] == 86400


def test_open_span_is_capped_at_completion_not_at_now():
    """A completed ticket cannot still be waiting somewhere.

    Reachable two ways: a state the sync could not resolve (state_type
    "unknown" never matches the done check), and two transitions sharing a
    timestamp so the last one recorded is not the last one that happened.
    Without the cap, either produces months of fake open time on a ticket
    that closed in March.
    """
    ticket = make_ticket(
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-02T10:00:00Z"),
        current_state="Shipped",
        current_state_type="unknown",
    )
    history = [
        transition("To Do", "unstarted", "In Progress", "started", "2026-09-01T10:00:00Z"),
        # A renamed or deleted workflow state the sync could not classify.
        transition("In Progress", "started", "Shipped", "unknown", "2026-09-02T10:00:00Z"),
    ]

    spans = build_state_spans(ticket, history, dt("2026-12-01T00:00:00Z"))
    shipped = [s for s in spans if s.state == "Shipped"]

    # Zero-length, so no span at all — and crucially not three months.
    assert shipped == []


def test_open_span_still_runs_to_now_for_a_ticket_that_is_open():
    ticket = make_ticket(
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        completed_at=None,
        is_complete=False,
        current_state="Blocked",
        current_state_type="unstarted",
    )
    history = [
        transition("To Do", "unstarted", "Blocked", "unstarted", "2026-09-01T10:00:00Z"),
    ]

    span = build_state_spans(ticket, history, dt("2026-09-04T10:00:00Z"))[-1]

    assert span.state == "Blocked"
    assert span.is_open is True
    assert span.seconds == 3 * 86400


def test_whitespace_only_state_names_are_dropped():
    ticket = make_ticket(provider_created_at=dt("2026-09-01T09:00:00Z"))
    history = [
        transition("To Do", "unstarted", "   ", "started", "2026-09-01T10:00:00Z"),
        transition("   ", "started", "Done", "done", "2026-09-02T10:00:00Z"),
    ]

    assert [s.state for s in build_state_spans(
        ticket, history, dt("2026-09-22T00:00:00Z")
    )] == ["To Do"]


def test_span_order_is_stable_when_timestamps_tie():
    """Ties are real — one bulk action can move a story twice.

    The id tiebreak does not recover the true order, but the same request
    must not give two different answers.
    """
    ticket = make_ticket(provider_created_at=dt("2026-09-01T09:00:00Z"))
    a = transition("To Do", "unstarted", "In Progress", "started",
                   "2026-09-02T10:00:00Z", id="t-a")
    b = transition("In Progress", "started", "Review Requested", "started",
                   "2026-09-02T10:00:00Z", id="t-b")

    forwards = build_state_spans(ticket, [a, b], dt("2026-09-22T00:00:00Z"))
    backwards = build_state_spans(ticket, [b, a], dt("2026-09-22T00:00:00Z"))

    assert [s.state for s in forwards] == [s.state for s in backwards]


# --------------------------------------------------------------------------
# clip_spans_to_window
# --------------------------------------------------------------------------


def test_clipping_keeps_only_the_part_inside_the_window():
    """This is what stops one old story dominating a short window.

    An eight-month-old story still sitting in Backlog would otherwise
    contribute its whole eight months to a seven-day window's p95.
    """
    spans = build_state_spans(
        make_ticket(
            provider_created_at=dt("2026-01-01T00:00:00Z"),
            completed_at=None,
            is_complete=False,
            current_state="Backlog",
            current_state_type="backlog",
        ),
        [transition("Backlog", "backlog", "Backlog", "backlog", "2026-01-01T00:00:00Z")],
        dt("2026-09-22T00:00:00Z"),
    )
    # One open Backlog span of nearly nine months.
    assert spans[0].seconds > 200 * 86400

    clipped = clip_spans_to_window(
        spans, dt("2026-09-15T00:00:00Z"), dt("2026-09-22T00:00:00Z")
    )
    assert len(clipped) == 1
    assert clipped[0].seconds == 7 * 86400


def test_clipping_drops_spans_entirely_outside_the_window():
    spans = build_state_spans(
        SC_8765, SC_8765_HISTORY, dt("2026-09-22T00:00:00Z")
    )
    clipped = clip_spans_to_window(
        spans, dt("2026-09-10T00:00:00Z"), dt("2026-09-20T00:00:00Z")
    )
    assert clipped == []


def test_clipping_makes_a_past_window_stable():
    """Re-reading last month must give last month's answer.

    The open end is clipped at the window's end, not at "now", so the number
    does not creep every day. The module docstring promises this for
    throughput and cycle time; it has to hold for the state durations too.
    """
    ticket = make_ticket(
        provider_created_at=dt("2026-08-01T00:00:00Z"),
        completed_at=None,
        is_complete=False,
        current_state="Backlog",
        current_state_type="backlog",
    )
    history = [
        transition("Backlog", "backlog", "Backlog", "backlog", "2026-08-01T00:00:00Z")
    ]
    window = (dt("2026-08-01T00:00:00Z"), dt("2026-08-31T00:00:00Z"))

    today = clip_spans_to_window(
        build_state_spans(ticket, history, dt("2026-09-22T00:00:00Z")), *window
    )
    next_month = clip_spans_to_window(
        build_state_spans(ticket, history, dt("2026-10-22T00:00:00Z")), *window
    )

    assert today[0].seconds == next_month[0].seconds == 30 * 86400


def test_state_metrics_are_windowed_not_lifetime():
    """The service must clip, not just offer the ability to.

    sc-8765's Backlog stay is 6 days, all of it before 1 September. A window
    starting on 1 September must not report it.
    """
    service, _ = build_service(
        completed=[SC_8765],
        active=[SC_8765],
        open_tickets=[],
        transitions={str(SC_8765.id): SC_8765_HISTORY},
    )
    metrics = service.get_flow_metrics(
        ORG_ID,
        Interval(dt("2026-09-01T00:00:00Z"), dt("2026-09-06T23:59:59Z")),
        TicketFilter(),
        as_of=dt("2026-09-22T00:00:00Z"),
    )

    states = {m.state: m.total_seconds for m in metrics.state_metrics}
    # 26 Aug 14:19 -> 1 Sep 00:00 falls outside; 1 Sep 00:00 -> 14:55 is in.
    assert states["Backlog"] == BACKLOG_SECONDS - (
        int((dt("2026-09-01T00:00:00Z") - dt(SC_8765_CREATED)).total_seconds())
    )
    assert states["Backlog"] < BACKLOG_SECONDS
    assert states["Review Requested"] == REVIEW_SECONDS


# --------------------------------------------------------------------------
# summarise_durations / percentiles
# --------------------------------------------------------------------------


def test_percentile_matches_linear_interpolation():
    values = [1, 2, 3, 4]
    # numpy.percentile([1,2,3,4], 50) == 2.5 -> int 2
    assert _percentile(values, 50) == 2
    assert _percentile(values, 0) == 1
    assert _percentile(values, 100) == 4


def test_percentile_of_single_value_is_that_value():
    assert _percentile([7], 95) == 7


def test_percentile_of_empty_is_none():
    assert _percentile([], 50) is None


def test_summarise_durations():
    stats = summarise_durations([10, 20, 30, 40, 50])

    assert stats.count == 5
    assert stats.mean == 30
    assert stats.p50 == 30
    assert stats.minimum == 10
    assert stats.maximum == 50


def test_summarise_durations_of_nothing_is_empty_not_zero():
    """An absent metric must not read as a measured zero."""
    stats = summarise_durations([])

    assert stats.count == 0
    assert stats.mean is None
    assert stats.p50 is None


# --------------------------------------------------------------------------
# TicketFilter
# --------------------------------------------------------------------------


def test_filter_defaults_exclude_cancelled_and_subtasks():
    ticket_filter = apply_ticket_filter({})

    assert ticket_filter.exclude_cancelled is True
    assert ticket_filter.include_subtasks is False


def test_filter_parses_team_ids_and_ignores_unknown_keys():
    ticket_filter = apply_ticket_filter(
        {"provider_team_ids": [SKIPPER], "not_a_real_key": "x"}
    )

    assert ticket_filter.provider_team_ids == [SKIPPER]
    assert ticket_filter.is_scoped_to_team is True


def test_filter_coerces_a_bare_string_to_a_list():
    assert apply_ticket_filter({"provider_team_ids": SKIPPER}).provider_team_ids == [
        SKIPPER
    ]


def test_filter_treats_empty_lists_as_absent():
    ticket_filter = apply_ticket_filter({"provider_team_ids": [], "epic_ids": [""]})

    assert ticket_filter.provider_team_ids is None
    assert ticket_filter.epic_ids is None
    assert ticket_filter.is_scoped_to_team is False


def test_filter_accepts_string_booleans_from_the_query_string():
    assert apply_ticket_filter({"include_subtasks": "true"}).include_subtasks is True
    assert apply_ticket_filter({"exclude_cancelled": "false"}).exclude_cancelled is False


# --------------------------------------------------------------------------
# get_flow_metrics
# --------------------------------------------------------------------------


def build_service(completed, active, open_tickets, transitions, links=None, epics=None):
    repo = MagicMock()
    repo.get_tickets_completed_in_interval.return_value = completed
    repo.get_tickets_active_in_interval.return_value = active
    repo.get_open_tickets.return_value = open_tickets
    repo.get_transitions_for_tickets.return_value = transitions
    repo.get_pull_request_links_for_tickets.return_value = links or {}
    repo.get_tickets_for_epics.return_value = epics or []
    return TicketAnalyticsService(repo), repo


INTERVAL = Interval(dt("2026-08-24T00:00:00Z"), dt("2026-09-06T23:59:59Z"))


def test_flow_metrics_for_the_real_story():
    service, _ = build_service(
        completed=[SC_8765],
        active=[SC_8765],
        open_tickets=[],
        transitions={str(SC_8765.id): SC_8765_HISTORY},
    )

    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.throughput == 1
    assert metrics.cycle_time.count == 1
    assert metrics.cycle_time.p50 == CYCLE_SECONDS
    assert metrics.lead_time.p50 == LEAD_SECONDS
    # The backlog wait DORA cannot see.
    assert metrics.queue_time.p50 == BACKLOG_SECONDS
    assert metrics.bug_ratio == 100.0
    assert metrics.completed_by_type == {"bug": 1}


def test_state_metrics_are_ordered_by_where_the_time_goes():
    service, _ = build_service(
        completed=[SC_8765],
        active=[SC_8765],
        open_tickets=[],
        transitions={str(SC_8765.id): SC_8765_HISTORY},
    )

    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert [m.state for m in metrics.state_metrics] == [
        "Backlog",
        "Review Requested",
        "In Progress",
    ]
    assert metrics.state_metrics[0].total_seconds == BACKLOG_SECONDS


def test_state_metrics_aggregate_across_workflows_by_name():
    """Zinc runs four workflows with different ids for the same state name.

    "How long does review take" is a question about the team, so the
    aggregation keys on the state name, not the workflow state id.
    """
    standard = make_ticket(
        id="aaaaaaaa-0000-0000-0000-000000000000",
        workflow_id="500000005",
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-02T10:00:00Z"),
    )
    with_qa = make_ticket(
        id="bbbbbbbb-0000-0000-0000-000000000000",
        workflow_id="500000847",
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-03T10:00:00Z"),
    )
    transitions = {
        str(standard.id): [
            transition("To Do", "unstarted", "Review Requested", "started", "2026-09-01T10:00:00Z"),
            transition("Review Requested", "started", "Done", "done", "2026-09-02T10:00:00Z"),
        ],
        str(with_qa.id): [
            transition("To Do", "unstarted", "Review Requested", "started", "2026-09-01T10:00:00Z"),
            transition("Review Requested", "started", "Done", "done", "2026-09-03T10:00:00Z"),
        ],
    }

    service, _ = build_service(
        completed=[standard, with_qa],
        active=[standard, with_qa],
        open_tickets=[],
        transitions=transitions,
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    review = next(m for m in metrics.state_metrics if m.state == "Review Requested")
    assert review.tickets == 2
    assert review.visits == 2
    assert review.total_seconds == 86400 + 2 * 86400
    # Mean is over tickets, not visits.
    assert review.duration.count == 2
    assert metrics.tickets_by_workflow == {"500000005": 1, "500000847": 1}


def test_review_mean_counts_each_ticket_once_however_often_it_bounced():
    """One story that bounced five times must not weigh five times.

    Aggregating over visits would make a single rework-heavy story look like
    five separate slow reviews and drag the average down twice over.
    """
    bouncer = make_ticket(
        id="cccccccc-0000-0000-0000-000000000000",
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-05T10:00:00Z"),
    )
    clean = make_ticket(
        id="dddddddd-0000-0000-0000-000000000000",
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-02T10:00:00Z"),
    )
    transitions = {
        str(bouncer.id): [
            transition("To Do", "unstarted", "Review Requested", "started", "2026-09-01T10:00:00Z"),
            transition("Review Requested", "started", "In Progress", "started", "2026-09-01T22:00:00Z"),
            transition("In Progress", "started", "Review Requested", "started", "2026-09-02T10:00:00Z"),
            transition("Review Requested", "started", "Done", "done", "2026-09-02T22:00:00Z"),
        ],
        str(clean.id): [
            transition("To Do", "unstarted", "Review Requested", "started", "2026-09-01T10:00:00Z"),
            transition("Review Requested", "started", "Done", "done", "2026-09-01T16:00:00Z"),
        ],
    }

    service, _ = build_service(
        completed=[bouncer, clean],
        active=[bouncer, clean],
        open_tickets=[],
        transitions=transitions,
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    review = next(m for m in metrics.state_metrics if m.state == "Review Requested")
    assert review.visits == 3
    assert review.tickets == 2
    # bouncer: 12h + 12h = 24h. clean: 6h.
    assert review.duration.count == 2
    assert review.duration.maximum == 24 * 3600
    assert review.duration.minimum == 6 * 3600


def test_wip_excludes_blocked_because_zinc_types_blocked_as_unstarted():
    in_progress = make_ticket(
        id="eeeeeeee-0000-0000-0000-000000000000",
        completed_at=None,
        is_complete=False,
        current_state="In Progress",
        current_state_type="started",
    )
    blocked = make_ticket(
        id="ffffffff-0000-0000-0000-000000000000",
        completed_at=None,
        is_complete=False,
        current_state="Blocked",
        current_state_type="unstarted",
    )

    service, _ = build_service(
        completed=[], active=[], open_tickets=[in_progress, blocked], transitions={}
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.open_total == 2
    assert metrics.wip == 1
    assert metrics.blocked_now == 1


def test_blocked_time_is_summed_per_ticket():
    ticket = make_ticket(
        id="12121212-0000-0000-0000-000000000000",
        completed_at=dt("2026-09-05T10:00:00Z"),
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
    )
    history = [
        transition("To Do", "unstarted", "In Progress", "started", "2026-09-01T10:00:00Z"),
        transition("In Progress", "started", "Blocked", "unstarted", "2026-09-02T10:00:00Z"),
        transition("Blocked", "unstarted", "In Progress", "started", "2026-09-03T10:00:00Z"),
        transition("In Progress", "started", "Blocked", "unstarted", "2026-09-04T10:00:00Z"),
        transition("Blocked", "unstarted", "Done", "done", "2026-09-05T10:00:00Z"),
    ]

    service, _ = build_service(
        completed=[ticket],
        active=[ticket],
        open_tickets=[],
        transitions={str(ticket.id): history},
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.blocked_time.count == 1
    assert metrics.blocked_time.p50 == 2 * 86400


def test_completed_without_start_is_counted_not_hidden():
    """Zinc closes stories that were never moved into a started state.

    Those cannot have a cycle time. Counting them as zero would flatter the
    average, so they are excluded from the stats and reported separately.
    """
    straight_to_done = make_ticket(
        id="13131313-0000-0000-0000-000000000000",
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=None,
        completed_at=dt("2026-09-02T09:00:00Z"),
    )
    history = [
        transition("To Do", "unstarted", "Done", "done", "2026-09-02T09:00:00Z"),
    ]

    service, _ = build_service(
        completed=[straight_to_done],
        active=[straight_to_done],
        open_tickets=[],
        transitions={str(straight_to_done.id): history},
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.throughput == 1
    assert metrics.completed_without_start == 1
    assert metrics.cycle_time.count == 0
    assert metrics.cycle_time.p50 is None
    # Lead time still works — creation and completion are both known.
    assert metrics.lead_time.count == 1


def test_completed_without_transitions_is_counted():
    no_history = make_ticket(
        id="14141414-0000-0000-0000-000000000000",
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-02T09:00:00Z"),
    )

    service, _ = build_service(
        completed=[no_history], active=[no_history], open_tickets=[], transitions={}
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.completed_without_transitions == 1
    assert metrics.state_metrics == []
    # started_at and completed_at are enough for cycle time without history.
    assert metrics.cycle_time.count == 1


def test_throughput_by_week_fills_empty_weeks():
    service, _ = build_service(
        completed=[SC_8765],
        active=[SC_8765],
        open_tickets=[],
        transitions={str(SC_8765.id): SC_8765_HISTORY},
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    # A flat line of zeroes with one spike is the point of a trend chart; a
    # dict with a single key is not.
    assert len(metrics.throughput_by_week) >= 2
    assert sum(metrics.throughput_by_week.values()) == 1


def test_unestimated_completed_is_counted():
    estimated = make_ticket(
        id="15151515-0000-0000-0000-000000000000",
        estimate=3,
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-02T09:00:00Z"),
    )
    unestimated = make_ticket(
        id="16161616-0000-0000-0000-000000000000",
        estimate=None,
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
        completed_at=dt("2026-09-02T09:00:00Z"),
    )

    service, _ = build_service(
        completed=[estimated, unestimated],
        active=[estimated, unestimated],
        open_tickets=[],
        transitions={},
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.unestimated_completed == 1


# --------------------------------------------------------------------------
# Epics
# --------------------------------------------------------------------------


def test_epic_progress_splits_cancelled_out_of_the_denominator():
    """"38 of 52" has to mean something.

    Dropping cancelled stories from the total silently would make an epic
    look further along than it is; counting them as outstanding would make it
    look worse. They are reported as their own number.
    """
    epic_id = "epic-1"
    done = make_ticket(
        id="17171717-0000-0000-0000-000000000000",
        provider_epic_id=epic_id,
        estimate=2,
        completed_at=dt("2026-09-02T09:00:00Z"),
    )
    doing = make_ticket(
        id="18181818-0000-0000-0000-000000000000",
        provider_epic_id=epic_id,
        estimate=3,
        completed_at=None,
        current_state="In Progress",
        current_state_type="started",
    )
    stuck = make_ticket(
        id="19191919-0000-0000-0000-000000000000",
        provider_epic_id=epic_id,
        estimate=None,
        completed_at=None,
        current_state="Blocked",
        current_state_type="unstarted",
    )
    todo = make_ticket(
        id="1a1a1a1a-0000-0000-0000-000000000000",
        provider_epic_id=epic_id,
        estimate=1,
        completed_at=None,
        current_state="To Do",
        current_state_type="unstarted",
    )
    scrapped = make_ticket(
        id="1b1b1b1b-0000-0000-0000-000000000000",
        provider_epic_id=epic_id,
        is_cancelled=True,
        current_state="Cancelled ",
        current_state_type="done",
        completed_at=dt("2026-09-02T09:00:00Z"),
    )

    service, _ = build_service(
        completed=[done],
        active=[done, doing, stuck, todo, scrapped],
        open_tickets=[doing, stuck, todo],
        transitions={},
        epics=[done, doing, stuck, todo, scrapped],
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    epic = metrics.epics[0]
    assert epic.total == 4
    assert epic.completed == 1
    assert epic.in_progress == 1
    assert epic.blocked == 1
    assert epic.not_started == 1
    assert epic.cancelled == 1
    assert epic.percent_complete == 25.0
    assert epic.estimate_total == 6
    assert epic.estimate_completed == 2
    assert epic.unestimated == 1


def test_epic_query_asks_for_cancelled_tickets():
    """The epic query must override the default exclusion, or the count above
    is impossible to produce."""
    service, repo = build_service(
        completed=[], active=[], open_tickets=[], transitions={}
    )
    service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    epic_filter = repo.get_tickets_for_epics.call_args[0][2]
    assert epic_filter.exclude_cancelled is False
    # ...without mutating the caller's filter.
    main_filter = repo.get_tickets_completed_in_interval.call_args[0][2]
    assert main_filter.exclude_cancelled is True


# --------------------------------------------------------------------------
# Iterations
# --------------------------------------------------------------------------


def test_iterations_are_empty_for_zinc_today():
    """Zinc has no Shortcut iterations; every story has iteration_id null."""
    service, _ = build_service(
        completed=[SC_8765],
        active=[SC_8765],
        open_tickets=[],
        transitions={str(SC_8765.id): SC_8765_HISTORY},
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.iterations == []


def test_iterations_work_when_zinc_starts_using_them():
    sprinted = make_ticket(
        id="1c1c1c1c-0000-0000-0000-000000000000",
        provider_iteration_id="iter-1",
        estimate=5,
        completed_at=dt("2026-09-02T09:00:00Z"),
    )
    in_flight = make_ticket(
        id="1d1d1d1d-0000-0000-0000-000000000000",
        provider_iteration_id="iter-1",
        estimate=3,
        completed_at=None,
    )

    service, _ = build_service(
        completed=[sprinted],
        active=[sprinted, in_flight],
        open_tickets=[in_flight],
        transitions={},
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    iteration = metrics.iterations[0]
    assert iteration.total == 2
    assert iteration.completed == 1
    assert iteration.estimate_total == 8
    assert iteration.estimate_completed == 5


# --------------------------------------------------------------------------
# Ticket <-> PR linkage
# --------------------------------------------------------------------------


def test_pull_request_linkage_reports_resolution_separately_from_linking():
    """Two different failures, two different numbers.

    A ticket with no link at all means Shortcut recorded no PR. A link with
    no pull_request_id means Shortcut recorded one but Middleware has not
    synced that repo — a different problem with a different fix.
    """
    linked = make_ticket(
        id="1e1e1e1e-0000-0000-0000-000000000000",
        completed_at=dt("2026-09-02T09:00:00Z"),
    )
    unlinked = make_ticket(
        id="1f1f1f1f-0000-0000-0000-000000000000",
        completed_at=dt("2026-09-02T09:00:00Z"),
    )
    links = {
        str(linked.id): [
            SimpleNamespace(pr_number=6906, repo_name="mvp-api", pull_request_id="pr-1"),
            SimpleNamespace(pr_number=6907, repo_name="mvp-api", pull_request_id=None),
        ]
    }

    service, _ = build_service(
        completed=[linked, unlinked],
        active=[],
        open_tickets=[],
        transitions={},
        links=links,
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    linkage = metrics.pull_request_linkage
    assert linkage.tickets == 2
    assert linkage.tickets_with_link == 1
    assert linkage.links_total == 2
    assert linkage.links_resolved_to_pull_request == 1
    assert linkage.link_rate == 50.0
    assert linkage.resolution_rate == 50.0


def test_linkage_rates_are_none_rather_than_zero_when_there_is_nothing_to_rate():
    service, _ = build_service(
        completed=[], active=[], open_tickets=[], transitions={}
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.pull_request_linkage.link_rate is None
    assert metrics.pull_request_linkage.resolution_rate is None


# --------------------------------------------------------------------------
# Empty org
# --------------------------------------------------------------------------


def test_empty_org_returns_zeroes_and_nones_without_raising():
    service, _ = build_service(
        completed=[], active=[], open_tickets=[], transitions={}
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.throughput == 0
    assert metrics.bug_ratio is None
    assert metrics.cycle_time.p50 is None
    assert metrics.state_metrics == []
    assert metrics.epics == []
    assert metrics.wip == 0


def test_a_past_window_gives_the_same_answer_next_month():
    """The service must clip the open end at the window, not at now.

    An open story's Backlog time grows every day. If the window's end is not
    the cap, last month's report changes every time it is opened — and the
    module docstring sells the opposite.
    """
    stuck = make_ticket(
        id="2a2a2a2a-0000-0000-0000-000000000000",
        provider_created_at=dt("2026-08-01T00:00:00Z"),
        completed_at=None,
        is_complete=False,
        current_state="Backlog",
        current_state_type="backlog",
    )
    history = {
        str(stuck.id): [
            transition("Backlog", "backlog", "Backlog", "backlog", "2026-08-01T00:00:00Z")
        ]
    }
    august = Interval(dt("2026-08-01T00:00:00Z"), dt("2026-08-31T00:00:00Z"))

    service, _ = build_service(
        completed=[], active=[stuck], open_tickets=[stuck], transitions=history
    )
    now = service.get_flow_metrics(
        ORG_ID, august, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )
    later = service.get_flow_metrics(
        ORG_ID, august, TicketFilter(), as_of=dt("2026-12-22T00:00:00Z")
    )

    assert now.state_metrics[0].total_seconds == 30 * 86400
    assert (
        now.state_metrics[0].total_seconds == later.state_metrics[0].total_seconds
    )


def test_state_names_aggregate_across_casing_and_trailing_spaces():
    """Zinc's own workflows contain "Cancelled " with a trailing space.

    Keying on the raw name would split one logical state into two rows, each
    showing half the real time and neither showing the real p95 — which
    defeats the reason the metrics key on name rather than on workflow state
    id in the first place.
    """
    a = make_ticket(
        id="21212121-0000-0000-0000-000000000000",
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        completed_at=dt("2026-09-03T10:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
    )
    b = make_ticket(
        id="22222222-0000-0000-0000-000000000000",
        provider_created_at=dt("2026-09-01T09:00:00Z"),
        completed_at=dt("2026-09-03T10:00:00Z"),
        started_at=dt("2026-09-01T10:00:00Z"),
    )
    transitions = {
        str(a.id): [
            transition("To Do", "unstarted", "Review Requested", "started", "2026-09-01T10:00:00Z"),
            transition("Review Requested", "started", "Done", "done", "2026-09-02T10:00:00Z"),
        ],
        str(b.id): [
            # Same state, different spelling.
            transition("To Do", "unstarted", "review requested ", "started", "2026-09-01T10:00:00Z"),
            transition("review requested ", "started", "Done", "done", "2026-09-02T10:00:00Z"),
        ],
    }

    service, _ = build_service(
        completed=[a, b], active=[a, b], open_tickets=[], transitions=transitions
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    review = [m for m in metrics.state_metrics if "review" in m.state.lower()]
    assert len(review) == 1
    assert review[0].tickets == 2


def test_tickets_by_workflow_describes_the_completed_set():
    """It exists to show whether THROUGHPUT is contaminated.

    Throughput counts completed tickets, so counting the split over active
    tickets would answer a different question from the one it is printed
    next to.
    """
    delivered = make_ticket(
        id="23232323-0000-0000-0000-000000000000",
        workflow_id="500000005",
        completed_at=dt("2026-09-02T09:00:00Z"),
    )
    theme = make_ticket(
        id="24242424-0000-0000-0000-000000000000",
        workflow_id="500000529",  # Mid-Longterm themes — not delivery work
        completed_at=None,
    )

    service, _ = build_service(
        completed=[delivered],
        active=[delivered, theme],
        open_tickets=[theme],
        transitions={},
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.tickets_by_workflow == {"500000005": 1}


def test_unresolvable_current_state_is_counted_not_assumed():
    """A state the sync could not classify is in neither wip nor blocked.

    Reporting wip=0 without saying so would be indistinguishable from
    "nothing in progress".
    """
    mystery = make_ticket(
        id="25252525-0000-0000-0000-000000000000",
        completed_at=None,
        is_complete=False,
        current_state="Shipped",
        current_state_type="unknown",
    )

    service, _ = build_service(
        completed=[], active=[], open_tickets=[mystery], transitions={}
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.open_total == 1
    assert metrics.wip == 0
    assert metrics.blocked_now == 0
    assert metrics.open_state_unknown == 1


def test_active_tickets_with_no_usable_history_are_counted():
    no_history = make_ticket(
        id="26262626-0000-0000-0000-000000000000",
        completed_at=None,
        is_complete=False,
    )

    service, _ = build_service(
        completed=[], active=[no_history], open_tickets=[no_history], transitions={}
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.state_metrics == []
    assert metrics.active_without_state_history == 1


def test_contradictory_timestamps_are_counted_not_clamped():
    """Completed before created. The provider's own clocks disagreeing.

    Clamping to zero would put a fake zero in the sample; dropping it
    silently would make cycle_time.count unexplainable against throughput.
    """
    backwards = make_ticket(
        id="27272727-0000-0000-0000-000000000000",
        provider_created_at=dt("2026-09-03T09:00:00Z"),
        started_at=dt("2026-09-03T10:00:00Z"),
        completed_at=dt("2026-09-02T09:00:00Z"),
    )

    service, _ = build_service(
        completed=[backwards], active=[backwards], open_tickets=[], transitions={}
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.throughput == 1
    assert metrics.cycle_time.count == 0
    assert metrics.lead_time.count == 0
    assert metrics.inconsistent_timestamps == 2


def test_epics_are_limited_to_those_touched_in_the_window():
    """All-time counts, but only for epics the team is actually working on.

    Otherwise the list is every epic the org has ever had, sorted by size, so
    the biggest historical ones sit at the top and the current work is buried.
    """
    current_epic_ticket = make_ticket(
        id="28282828-0000-0000-0000-000000000000",
        provider_epic_id="epic-current",
        completed_at=dt("2026-09-02T09:00:00Z"),
    )
    old_epic_ticket = make_ticket(
        id="29292929-0000-0000-0000-000000000000",
        provider_epic_id="epic-ancient",
        completed_at=dt("2024-01-02T09:00:00Z"),
    )

    service, _ = build_service(
        completed=[current_epic_ticket],
        active=[current_epic_ticket],
        open_tickets=[],
        transitions={},
        epics=[current_epic_ticket, old_epic_ticket],
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert [e.epic_id for e in metrics.epics] == ["epic-current"]


def test_as_of_is_reported():
    service, _ = build_service(
        completed=[], active=[], open_tickets=[], transitions={}
    )
    metrics = service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert metrics.as_of == dt("2026-09-22T00:00:00Z")


def test_a_missing_filter_still_applies_the_default_exclusions():
    """None must mean "the whole org, usual exclusions", not "no exclusions"."""
    service, repo = build_service(
        completed=[], active=[], open_tickets=[], transitions={}
    )
    service.get_flow_metrics(
        ORG_ID, INTERVAL, None, as_of=dt("2026-09-22T00:00:00Z")
    )

    used = repo.get_tickets_completed_in_interval.call_args[0][2]
    assert used.exclude_cancelled is True
    assert used.include_subtasks is False


def test_transitions_are_fetched_once_for_the_union_of_both_windows():
    """A ticket in both windows must not be queried twice.

    A 90-day window for a Zinc squad is a few hundred stories; per-ticket
    queries would make this endpoint too slow to use.
    """
    also_active = make_ticket(
        id="20202020-0000-0000-0000-000000000000",
        completed_at=None,
    )
    service, repo = build_service(
        completed=[SC_8765],
        active=[SC_8765, also_active],
        open_tickets=[also_active],
        transitions={},
    )
    service.get_flow_metrics(
        ORG_ID, INTERVAL, TicketFilter(), as_of=dt("2026-09-22T00:00:00Z")
    )

    assert repo.get_transitions_for_tickets.call_count == 1
    requested = repo.get_transitions_for_tickets.call_args[0][0]
    assert sorted(requested) == sorted({str(SC_8765.id), str(also_active.id)})
