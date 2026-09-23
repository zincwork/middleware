"""Value objects for the ticket flow metrics.

Plain dataclasses with no SQLAlchemy and no Flask, so the algorithms in
analytics.py can be tested with hand-built objects and no database.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class StateSpan:
    """One continuous stay in one workflow state.

    A ticket that goes In Progress -> Review Requested -> In Progress ->
    Review Requested produces four spans, two of them for Review Requested.
    Summing per state name across spans is what makes the review wait real
    rather than only counting the last attempt.
    """

    state: str
    state_type: str
    entered_at: datetime
    left_at: datetime
    # True when the ticket is still sitting here, so left_at is "now" rather
    # than an observed transition. Kept so the API can say which figures are
    # still accruing.
    is_open: bool = False

    @property
    def seconds(self) -> int:
        # max() guards against provider clock skew, not against a bug here —
        # the spans are built from a sorted list, so our own ordering cannot
        # produce a negative. Shortcut's own event timestamps can disagree.
        return max(0, int((self.left_at - self.entered_at).total_seconds()))


@dataclass
class DurationStats:
    """Summary of a list of durations, in seconds."""

    count: int = 0
    mean: Optional[int] = None
    p50: Optional[int] = None
    p75: Optional[int] = None
    p95: Optional[int] = None
    minimum: Optional[int] = None
    maximum: Optional[int] = None


@dataclass
class StateFlowMetric:
    """How long work waits in one workflow state."""

    state: str
    state_type: str
    # Distinct tickets that entered this state at least once.
    tickets: int = 0
    # Total stays, which exceeds `tickets` when work bounces back.
    visits: int = 0
    total_seconds: int = 0
    # Per-ticket totals (every visit summed), then summarised. Mean and
    # percentiles are over tickets, not over visits, so one story that
    # bounced five times does not count five times.
    duration: DurationStats = field(default_factory=DurationStats)
    # Tickets still sitting in this state right now.
    open_tickets: int = 0


@dataclass
class EpicProgress:
    epic_id: str
    total: int = 0
    completed: int = 0
    in_progress: int = 0
    blocked: int = 0
    not_started: int = 0
    cancelled: int = 0
    # Tickets whose current state the sync could not resolve. Kept apart from
    # not_started so missing data is not read as a measured state.
    state_unknown: int = 0
    estimate_total: Optional[int] = None
    estimate_completed: Optional[int] = None
    unestimated: int = 0

    @property
    def percent_complete(self) -> Optional[float]:
        if not self.total:
            return None
        return round(100.0 * self.completed / self.total, 1)


@dataclass
class IterationProgress:
    iteration_id: str
    total: int = 0
    completed: int = 0
    estimate_total: Optional[int] = None
    estimate_completed: Optional[int] = None


@dataclass
class PullRequestLinkage:
    """How well the ticket-to-PR join is working.

    This is the diagnostic that says whether the Cockpit view has anything to
    stand on: a ticket with no linked PR cannot be tied to a deployment, so a
    low `tickets_with_link` means the join needs looking at before the
    combined view is worth trusting.
    """

    tickets: int = 0
    tickets_with_link: int = 0
    links_total: int = 0
    links_resolved_to_pull_request: int = 0

    @property
    def link_rate(self) -> Optional[float]:
        if not self.tickets:
            return None
        return round(100.0 * self.tickets_with_link / self.tickets, 1)

    @property
    def resolution_rate(self) -> Optional[float]:
        if not self.links_total:
            return None
        return round(
            100.0 * self.links_resolved_to_pull_request / self.links_total, 1
        )


@dataclass
class FlowMetrics:
    """Everything the Cockpit view needs for one team over one window."""

    # The moment the point-in-time figures were taken, and the cap on the
    # open end of every state duration. Echoed in the response so a figure
    # can be told apart from a fresher one.
    as_of: Optional[datetime] = None

    # Throughput
    throughput: int = 0
    throughput_by_week: Dict[datetime, int] = field(default_factory=dict)

    # Elapsed times, over tickets completed in the window
    cycle_time: DurationStats = field(default_factory=DurationStats)
    lead_time: DurationStats = field(default_factory=DurationStats)
    queue_time: DurationStats = field(default_factory=DurationStats)

    # Where the time goes, over tickets active in the window
    state_metrics: List[StateFlowMetric] = field(default_factory=list)
    blocked_time: DurationStats = field(default_factory=DurationStats)

    # Mix
    completed_by_type: Dict[str, int] = field(default_factory=dict)
    bug_ratio: Optional[float] = None

    # Point-in-time
    wip: int = 0
    blocked_now: int = 0
    open_total: int = 0

    # Planning
    epics: List[EpicProgress] = field(default_factory=list)
    iterations: List[IterationProgress] = field(default_factory=list)

    # Joins and data quality
    pull_request_linkage: PullRequestLinkage = field(
        default_factory=PullRequestLinkage
    )
    completed_without_start: int = 0
    completed_without_transitions: int = 0
    # Active tickets with no usable history in the window, and so no
    # contribution to state_metrics. Without this the state table is computed
    # over an unknown subset of the counts printed above it.
    active_without_state_history: int = 0
    # Tickets whose provider timestamps contradict each other (completed
    # before started, or before created). Excluded from the affected sample
    # rather than clamped to zero.
    inconsistent_timestamps: int = 0
    # Open tickets whose current state the sync could not resolve, and which
    # are therefore in neither the WIP nor the blocked count.
    open_state_unknown: int = 0
    tickets_by_workflow: Dict[str, int] = field(default_factory=dict)
    unestimated_completed: int = 0
