"""Ticket flow metrics — the numbers behind the Cockpit view.

The one idea this file rests on: Middleware's existing DORA metrics are
measured on pull requests and deployments, which start at the first commit.
Everything before that — sitting in To Do, waiting on product, blocked on
another team — is invisible to them. Shortcut's state history is the only
record of that time, so these metrics are computed from transitions rather
than from the ticket's current state.

Two windows are in play, deliberately:

  * Completed-in-window drives throughput, cycle time, lead time and bug
    ratio. Measuring on completion date means a past week's number never
    changes retrospectively, which is the convention the deployment and
    incident metrics already follow.

  * Active-in-window drives the per-state durations. A story that has sat in
    Review Requested for three weeks and is still sitting there is exactly
    the thing a manager needs to see, and it would be missing entirely from a
    completed-only view.

Nothing in this file writes. It is a read path over the tables Layer 1 fills.
"""

import math
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from mhq.service.tickets.flow_models import (
    DurationStats,
    EpicProgress,
    FlowMetrics,
    IterationProgress,
    PullRequestLinkage,
    StateFlowMetric,
    StateSpan,
)
from mhq.store.models.tickets.enums import TicketProviders, TicketStateType, TicketType
from mhq.store.models.tickets.filter import TicketFilter
from mhq.store.models.tickets.tickets import Ticket, TicketStateTransition
from mhq.store.repos.ticket_analytics import (
    TicketAnalyticsRepoService,
    get_ticket_analytics_repo_service,
)
from mhq.utils.time import Interval, generate_expanded_buckets, time_now

# Zinc names this state exactly "Blocked" in three of its four workflows and
# types it `unstarted` — so blocked work is NOT counted as started by
# Shortcut, and therefore not in the WIP figure below. It is reported
# separately instead of being folded in, because "six in progress" and "six
# in progress plus four blocked" call for different conversations.
BLOCKED_STATE_NAMES = {"blocked"}


def _normalise(name: Optional[str]) -> str:
    # Zinc's workflows contain "Cancelled " with a trailing space, so state
    # names are compared trimmed and lowercased throughout.
    return (name or "").strip().lower()


def _percentile(sorted_values: List[int], p: float) -> Optional[int]:
    """Percentile by linear interpolation, truncated to whole seconds.

    Same interpolation method as numpy's default, so the figures line up with
    anything computed there — but the result is an int, so it can differ from
    numpy's by up to a second. Durations here are hours and days, so a second
    does not matter; the alternative is a float that implies precision the
    provider's timestamps do not have.

    Written out rather than pulled from a library because the analytics server
    has no numpy or scipy dependency, and adding one for six lines of
    arithmetic would be a poor trade.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return int(sorted_values[0])

    k = (len(sorted_values) - 1) * (p / 100.0)
    lower = int(math.floor(k))
    upper = int(math.ceil(k))
    if lower == upper:
        return int(sorted_values[lower])
    return int(
        sorted_values[lower] * (upper - k) + sorted_values[upper] * (k - lower)
    )


def summarise_durations(values: List[int]) -> DurationStats:
    if not values:
        return DurationStats()

    ordered = sorted(values)
    return DurationStats(
        count=len(ordered),
        mean=int(round(sum(ordered) / len(ordered))),
        p50=_percentile(ordered, 50),
        p75=_percentile(ordered, 75),
        p95=_percentile(ordered, 95),
        minimum=ordered[0],
        maximum=ordered[-1],
    )


def build_state_spans(
    ticket: Ticket,
    transitions: List[TicketStateTransition],
    as_of: datetime,
) -> List[StateSpan]:
    """Turn a ticket's transition list into the stays it implies.

    Three things are worth knowing about the result:

    1. The stay BEFORE the first recorded transition is included, taken from
       that transition's `from_state` and the ticket's creation time.
       Without it, time in Backlog or To Do is understated by its whole first
       visit — which for Zinc is usually the longest one.

    2. A repeat visit is its own span. Callers sum by state name, so a story
       that bounces In Progress -> Review Requested -> In Progress ->
       Review Requested reports the full review wait, not just the last leg.

    3. A final done-type state gets NO span, and a final non-done state is
       capped at the ticket's own completion time where it has one. A story
       closed in March would otherwise report five months "in Done" and
       dominate every chart; and a story whose last transition landed in a
       state the sync could not resolve (state_type "unknown") would accrue
       open time for ever.

    Spans cover the ticket's whole life. Callers that report on a window must
    clip them to it — see clip_spans_to_window.
    """
    # Tied timestamps are real: one bulk action can move a story twice, and
    # Shortcut can put two state changes in a single history entry. The id
    # tiebreak does not recover the true order, but it does make the result
    # the same on every request, which an arbitrary database row order would
    # not. The completion cap below is what stops a mis-ordered tie turning a
    # closed ticket into an open one.
    ordered = sorted(
        [t for t in transitions if t.changed_at],
        key=lambda t: (t.changed_at, str(getattr(t, "id", ""))),
    )
    if not ordered:
        return []

    spans: List[StateSpan] = []

    first = ordered[0]
    if (
        first.from_state
        and ticket.provider_created_at
        and first.changed_at > ticket.provider_created_at
    ):
        spans.append(
            StateSpan(
                state=first.from_state,
                state_type=first.from_state_type,
                entered_at=ticket.provider_created_at,
                left_at=first.changed_at,
            )
        )

    for current, following in zip(ordered, ordered[1:]):
        spans.append(
            StateSpan(
                state=current.to_state,
                state_type=current.to_state_type,
                entered_at=current.changed_at,
                left_at=following.changed_at,
            )
        )

    last = ordered[-1]
    if TicketStateType.from_provider(last.to_state_type) is not TicketStateType.DONE:
        # A completed ticket cannot still be waiting somewhere, whatever its
        # last transition says. Capping at completed_at covers both the
        # unresolvable-state case and a mis-ordered tie.
        closed_at = ticket.completed_at if ticket.completed_at else None
        left_at = min(as_of, closed_at) if closed_at else as_of
        # Strictly greater: a zero-length span would add a visit and a zero to
        # the duration sample, dragging the p50 down for no reason.
        if left_at > last.changed_at:
            spans.append(
                StateSpan(
                    state=last.to_state,
                    state_type=last.to_state_type,
                    entered_at=last.changed_at,
                    left_at=left_at,
                    is_open=closed_at is None,
                )
            )

    # A blank or whitespace-only state name would otherwise become an unnamed
    # row in the per-state table.
    return [span for span in spans if span.state and span.state.strip()]


def clip_spans_to_window(
    spans: List[StateSpan], window_from: datetime, window_to: datetime
) -> List[StateSpan]:
    """Restrict spans to the reporting window.

    Without this, a single eight-month-old open story contributes its entire
    Backlog stay to a seven-day window's p95, and the per-state figures stop
    describing the window they are presented alongside.

    Clipping the open end at `window_to` rather than at "now" is also what
    makes a past window stable: reload last month and the numbers are what
    they were, which is the property the throughput and cycle-time figures
    already have.
    """
    clipped: List[StateSpan] = []
    for span in spans:
        start = max(span.entered_at, window_from)
        end = min(span.left_at, window_to)
        if end <= start:
            continue
        clipped.append(
            StateSpan(
                state=span.state,
                state_type=span.state_type,
                entered_at=start,
                left_at=end,
                is_open=span.is_open,
            )
        )
    return clipped


def _first_started_at(
    ticket: Ticket, spans: List[StateSpan]
) -> Tuple[Optional[datetime], bool]:
    """When work actually began, and whether that is known at all.

    Prefers Shortcut's own `started_at`, which it maintains and clears if a
    story moves back out of a started state. Falls back to the first
    started-type span for stories synced before that field was populated.
    Note the two branches are not the same quantity for a story that left a
    started state and came back: the provider field is one specific start,
    the span fallback is the earliest one. The fallback only applies to
    tickets whose provider field is empty, so a given sample can contain both
    — worth knowing before reading cycle time to the nearest hour.
    """
    if ticket.started_at:
        return ticket.started_at, True

    for span in spans:
        if (
            TicketStateType.from_provider(span.state_type)
            is TicketStateType.STARTED
        ):
            return span.entered_at, True

    return None, False


class TicketAnalyticsService:
    def __init__(self, repo: TicketAnalyticsRepoService):
        self._repo = repo

    def get_flow_metrics(
        self,
        org_id: str,
        interval: Interval,
        ticket_filter: TicketFilter,
        provider: str = TicketProviders.SHORTCUT.value,
        as_of: Optional[datetime] = None,
    ) -> FlowMetrics:
        as_of = as_of or time_now()
        # An absent filter means "the whole org, with the usual exclusions",
        # not "no exclusions" — so it becomes a default TicketFilter rather
        # than being passed through as None.
        ticket_filter = ticket_filter or TicketFilter()

        completed = self._repo.get_tickets_completed_in_interval(
            org_id, provider, ticket_filter, interval.from_time, interval.to_time
        )
        active = self._repo.get_tickets_active_in_interval(
            org_id, provider, ticket_filter, interval.from_time, interval.to_time
        )
        open_tickets = self._repo.get_open_tickets(org_id, provider, ticket_filter)

        # One transitions query for the union, so a ticket that is both
        # completed in the window and active in it is fetched once.
        all_tickets = {str(t.id): t for t in list(completed) + list(active)}
        transitions = self._repo.get_transitions_for_tickets(list(all_tickets.keys()))
        spans_by_ticket = {
            ticket_id: build_state_spans(
                ticket, transitions.get(ticket_id, []), as_of
            )
            for ticket_id, ticket in all_tickets.items()
        }

        metrics = FlowMetrics()
        # Everything reported "as of" a moment is reported as of this one, and
        # it is echoed in the response so a stale figure is recognisable.
        metrics.as_of = as_of
        # The open end of the window. Clipping at to_time rather than at now
        # is what makes a past window give the same answer twice.
        window_to = min(as_of, interval.to_time)

        self._add_throughput_and_elapsed(
            metrics, completed, spans_by_ticket, transitions, interval
        )
        self._add_state_metrics(
            metrics, active, spans_by_ticket, interval.from_time, window_to
        )
        self._add_point_in_time(metrics, open_tickets)
        self._add_mix(metrics, completed)
        self._add_planning(
            metrics, org_id, provider, ticket_filter, active, completed
        )
        self._add_linkage(metrics, completed)

        # Counted over the COMPLETED set, because the question this answers is
        # whether throughput is contaminated by the "Mid-Longterm themes"
        # workflow, and throughput is a count of completed tickets.
        metrics.tickets_by_workflow = self._count_by(
            completed, lambda t: t.workflow_id or "unknown"
        )

        return metrics

    # ------------------------------------------------------------------
    # Throughput, cycle time, lead time, queue time
    # ------------------------------------------------------------------

    def _add_throughput_and_elapsed(
        self,
        metrics: FlowMetrics,
        completed: List[Ticket],
        spans_by_ticket: Dict[str, List[StateSpan]],
        transitions_by_ticket: Dict[str, List[TicketStateTransition]],
        interval: Interval,
    ) -> None:
        metrics.throughput = len(completed)

        buckets = generate_expanded_buckets(
            list(completed), interval, "completed_at", "weekly"
        )
        metrics.throughput_by_week = {
            week: len(tickets) for week, tickets in sorted(buckets.items())
        }

        cycle_times: List[int] = []
        lead_times: List[int] = []
        queue_times: List[int] = []

        for ticket in completed:
            ticket_id = str(ticket.id)
            spans = spans_by_ticket.get(ticket_id, [])
            if not transitions_by_ticket.get(ticket_id):
                metrics.completed_without_transitions += 1

            started_at, has_start = _first_started_at(ticket, spans)
            if not has_start:
                # Went straight from To Do to Done without ever being moved
                # into a started state. Real at Zinc, and it means cycle time
                # is computed over a subset — so the count is reported rather
                # than the ticket being silently treated as zero-duration.
                metrics.completed_without_start += 1
            elif ticket.completed_at >= started_at:
                cycle_times.append(
                    int((ticket.completed_at - started_at).total_seconds())
                )
            else:
                # completed before it started: the provider's timestamps
                # disagree. Excluded rather than clamped to zero, and counted
                # so the sample size is explicable.
                metrics.inconsistent_timestamps += 1

            if ticket.provider_created_at:
                if ticket.completed_at >= ticket.provider_created_at:
                    lead_times.append(
                        int(
                            (
                                ticket.completed_at - ticket.provider_created_at
                            ).total_seconds()
                        )
                    )
                else:
                    metrics.inconsistent_timestamps += 1
                if has_start and started_at >= ticket.provider_created_at:
                    queue_times.append(
                        int(
                            (started_at - ticket.provider_created_at).total_seconds()
                        )
                    )

            if ticket.estimate is None:
                metrics.unestimated_completed += 1

        metrics.cycle_time = summarise_durations(cycle_times)
        metrics.lead_time = summarise_durations(lead_times)
        metrics.queue_time = summarise_durations(queue_times)

    # ------------------------------------------------------------------
    # Where the time goes
    # ------------------------------------------------------------------

    def _add_state_metrics(
        self,
        metrics: FlowMetrics,
        active: List[Ticket],
        spans_by_ticket: Dict[str, List[StateSpan]],
        window_from: datetime,
        window_to: datetime,
    ) -> None:
        # Keyed on the NORMALISED state name, not the workflow state id, and
        # not the raw name. The id would split "Review Requested" into four
        # rows, one per workflow, when the question is about the team. The raw
        # name would split it again on "Cancelled " and on any casing
        # difference between workflows. The first spelling seen is kept for
        # display.
        totals: Dict[str, Dict] = {}
        per_ticket_seconds: Dict[str, List[int]] = defaultdict(list)
        blocked_seconds: List[int] = []

        for ticket in active:
            spans = clip_spans_to_window(
                spans_by_ticket.get(str(ticket.id), []), window_from, window_to
            )
            if not spans:
                # Either no usable history, or nothing that overlaps the
                # window. Counted, because otherwise the state table is
                # computed over an unknown subset of the tickets above it.
                metrics.active_without_state_history += 1
                continue

            ticket_state_seconds: Dict[str, int] = defaultdict(int)
            ticket_blocked = 0

            for span in spans:
                key = _normalise(span.state)
                entry = totals.setdefault(
                    key,
                    {
                        "state": span.state.strip(),
                        "state_type": span.state_type,
                        "tickets": set(),
                        "visits": 0,
                        "total_seconds": 0,
                        "open_tickets": set(),
                    },
                )
                entry["tickets"].add(str(ticket.id))
                entry["visits"] += 1
                entry["total_seconds"] += span.seconds
                if span.is_open:
                    entry["open_tickets"].add(str(ticket.id))

                ticket_state_seconds[key] += span.seconds
                if key in BLOCKED_STATE_NAMES:
                    ticket_blocked += span.seconds

            for key, seconds in ticket_state_seconds.items():
                per_ticket_seconds[key].append(seconds)
            if ticket_blocked:
                blocked_seconds.append(ticket_blocked)

        metrics.state_metrics = sorted(
            (
                StateFlowMetric(
                    state=entry["state"],
                    state_type=entry["state_type"],
                    tickets=len(entry["tickets"]),
                    visits=entry["visits"],
                    total_seconds=entry["total_seconds"],
                    duration=summarise_durations(per_ticket_seconds[key]),
                    open_tickets=len(entry["open_tickets"]),
                )
                for key, entry in totals.items()
            ),
            # Name as the tiebreak, so two states with identical totals come
            # back in the same order on every request.
            key=lambda m: (-m.total_seconds, m.state),
        )
        metrics.blocked_time = summarise_durations(blocked_seconds)

    # ------------------------------------------------------------------
    # Point-in-time counts
    # ------------------------------------------------------------------

    def _add_point_in_time(
        self, metrics: FlowMetrics, open_tickets: List[Ticket]
    ) -> None:
        metrics.open_total = len(open_tickets)
        metrics.wip = sum(
            1
            for ticket in open_tickets
            if TicketStateType.from_provider(ticket.current_state_type)
            is TicketStateType.STARTED
        )
        metrics.blocked_now = sum(
            1
            for ticket in open_tickets
            if _normalise(ticket.current_state) in BLOCKED_STATE_NAMES
        )
        # A ticket whose current state the sync could not resolve is in
        # neither count above, so a zero WIP would otherwise be
        # indistinguishable from "nothing in progress".
        metrics.open_state_unknown = sum(
            1
            for ticket in open_tickets
            if TicketStateType.from_provider(ticket.current_state_type)
            is TicketStateType.UNKNOWN
        )

    # ------------------------------------------------------------------
    # Mix
    # ------------------------------------------------------------------

    def _add_mix(self, metrics: FlowMetrics, completed: List[Ticket]) -> None:
        metrics.completed_by_type = self._count_by(
            completed, lambda t: t.ticket_type or TicketType.UNKNOWN.value
        )
        if completed:
            bugs = metrics.completed_by_type.get(TicketType.BUG.value, 0)
            metrics.bug_ratio = round(100.0 * bugs / len(completed), 1)

    # ------------------------------------------------------------------
    # Epics and iterations
    # ------------------------------------------------------------------

    def _add_planning(
        self,
        metrics: FlowMetrics,
        org_id: str,
        provider: str,
        ticket_filter: TicketFilter,
        active: List[Ticket],
        completed: List[Ticket],
    ) -> None:
        # Cancelled is queried back in (the default filter excludes it) so it
        # can be reported as its own number. It stays OUT of `total`:
        # abandoned work is not remaining work, so counting it would make an
        # epic look permanently behind. "38 done, 48 total, 4 cancelled" —
        # three numbers, no guessing which is which.
        epic_filter = replace(ticket_filter, exclude_cancelled=False)
        epic_tickets = self._repo.get_tickets_for_epics(
            org_id, provider, epic_filter
        )

        # Only epics this team has touched in the window. The counts within
        # each epic stay all-time, because "38 of 48" is a completeness
        # question — but listing every epic the org has ever had, sorted by
        # size, would put the largest historical ones at the top and bury the
        # current work.
        relevant = {
            ticket.provider_epic_id
            for ticket in list(active) + list(completed)
            if ticket.provider_epic_id
        }

        by_epic: Dict[str, EpicProgress] = {}
        for ticket in epic_tickets:
            if ticket.provider_epic_id not in relevant:
                continue
            progress = by_epic.setdefault(
                ticket.provider_epic_id,
                EpicProgress(epic_id=ticket.provider_epic_id),
            )

            if ticket.is_cancelled:
                progress.cancelled += 1
                continue

            progress.total += 1
            if ticket.estimate is None:
                progress.unestimated += 1
            else:
                progress.estimate_total = (progress.estimate_total or 0) + (
                    ticket.estimate
                )

            if ticket.completed_at:
                progress.completed += 1
                if ticket.estimate is not None:
                    progress.estimate_completed = (
                        progress.estimate_completed or 0
                    ) + ticket.estimate
            elif _normalise(ticket.current_state) in BLOCKED_STATE_NAMES:
                progress.blocked += 1
            elif (
                TicketStateType.from_provider(ticket.current_state_type)
                is TicketStateType.STARTED
            ):
                progress.in_progress += 1
            elif (
                TicketStateType.from_provider(ticket.current_state_type)
                is TicketStateType.UNKNOWN
            ):
                # Unresolvable state. Reporting it as "not started" would be
                # a measurement where there is only missing data.
                progress.state_unknown += 1
            else:
                progress.not_started += 1

        metrics.epics = sorted(
            by_epic.values(), key=lambda e: (-e.total, e.epic_id)
        )

        # Zinc has no Shortcut iterations today, so this is empty in practice.
        # The code path exists and is tested so that adopting sprints later is
        # a config change rather than a code change.
        #
        # Note the denominator differs from the epic one above: an iteration
        # is a window by definition, so it is counted over the tickets active
        # in the selected window, not all-time.
        by_iteration: Dict[str, IterationProgress] = {}
        for ticket in active:
            if not ticket.provider_iteration_id:
                continue
            progress = by_iteration.setdefault(
                ticket.provider_iteration_id,
                IterationProgress(iteration_id=ticket.provider_iteration_id),
            )
            progress.total += 1
            if ticket.estimate is not None:
                progress.estimate_total = (progress.estimate_total or 0) + (
                    ticket.estimate
                )
            if ticket.completed_at:
                progress.completed += 1
                if ticket.estimate is not None:
                    progress.estimate_completed = (
                        progress.estimate_completed or 0
                    ) + ticket.estimate

        metrics.iterations = sorted(
            by_iteration.values(), key=lambda i: i.iteration_id
        )

    # ------------------------------------------------------------------
    # Ticket <-> pull request join health
    # ------------------------------------------------------------------

    def _add_linkage(self, metrics: FlowMetrics, completed: List[Ticket]) -> None:
        ticket_ids = [str(t.id) for t in completed]
        links = self._repo.get_pull_request_links_for_tickets(ticket_ids)

        linkage = PullRequestLinkage(tickets=len(ticket_ids))
        for ticket_id in ticket_ids:
            rows = links.get(ticket_id, [])
            if rows:
                linkage.tickets_with_link += 1
            linkage.links_total += len(rows)
            linkage.links_resolved_to_pull_request += sum(
                1 for row in rows if row.pull_request_id
            )

        metrics.pull_request_linkage = linkage

    # ------------------------------------------------------------------

    @staticmethod
    def _count_by(tickets: List[Ticket], key) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for ticket in tickets:
            counts[key(ticket)] += 1
        return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def get_ticket_analytics_service() -> TicketAnalyticsService:
    return TicketAnalyticsService(get_ticket_analytics_repo_service())
