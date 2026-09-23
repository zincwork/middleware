"""JSON adapters for the ticket flow metrics.

Durations are emitted in seconds, as integers, because that is what the
existing PR and incident resources do — the frontend already has the
formatters for it.
"""

from typing import Dict, Optional

from mhq.service.tickets.flow_models import (
    DurationStats,
    EpicProgress,
    FlowMetrics,
    IterationProgress,
    PullRequestLinkage,
    StateFlowMetric,
)


def adapt_duration_stats(stats: DurationStats) -> Dict[str, Optional[int]]:
    return {
        "count": stats.count,
        "mean": stats.mean,
        "p50": stats.p50,
        "p75": stats.p75,
        "p95": stats.p95,
        "min": stats.minimum,
        "max": stats.maximum,
    }


def adapt_state_metric(metric: StateFlowMetric) -> Dict[str, any]:
    return {
        "state": metric.state,
        "state_type": metric.state_type,
        "tickets": metric.tickets,
        "visits": metric.visits,
        "total_seconds": metric.total_seconds,
        "open_tickets": metric.open_tickets,
        "duration": adapt_duration_stats(metric.duration),
    }


def adapt_epic_progress(epic: EpicProgress) -> Dict[str, any]:
    return {
        "epic_id": epic.epic_id,
        "total": epic.total,
        "completed": epic.completed,
        "in_progress": epic.in_progress,
        "blocked": epic.blocked,
        "not_started": epic.not_started,
        "cancelled": epic.cancelled,
        "state_unknown": epic.state_unknown,
        "percent_complete": epic.percent_complete,
        "estimate_total": epic.estimate_total,
        "estimate_completed": epic.estimate_completed,
        "unestimated": epic.unestimated,
    }


def adapt_iteration_progress(iteration: IterationProgress) -> Dict[str, any]:
    return {
        "iteration_id": iteration.iteration_id,
        "total": iteration.total,
        "completed": iteration.completed,
        "estimate_total": iteration.estimate_total,
        "estimate_completed": iteration.estimate_completed,
    }


def adapt_pull_request_linkage(linkage: PullRequestLinkage) -> Dict[str, any]:
    return {
        "tickets": linkage.tickets,
        "tickets_with_link": linkage.tickets_with_link,
        "links_total": linkage.links_total,
        "links_resolved_to_pull_request": linkage.links_resolved_to_pull_request,
        "link_rate": linkage.link_rate,
        "resolution_rate": linkage.resolution_rate,
    }


def adapt_flow_metrics(metrics: FlowMetrics) -> Dict[str, any]:
    return {
        # wip, blocked_now, open_total and the open end of every state
        # duration are all as of this moment, not as of the window's end.
        "as_of": metrics.as_of.isoformat() if metrics.as_of else None,
        "throughput": metrics.throughput,
        "throughput_by_week": {
            week.isoformat(): count
            for week, count in metrics.throughput_by_week.items()
        },
        "cycle_time": adapt_duration_stats(metrics.cycle_time),
        "lead_time": adapt_duration_stats(metrics.lead_time),
        "queue_time": adapt_duration_stats(metrics.queue_time),
        "state_metrics": [
            adapt_state_metric(metric) for metric in metrics.state_metrics
        ],
        "blocked_time": adapt_duration_stats(metrics.blocked_time),
        "completed_by_type": metrics.completed_by_type,
        "bug_ratio": metrics.bug_ratio,
        "wip": metrics.wip,
        "blocked_now": metrics.blocked_now,
        "open_total": metrics.open_total,
        "epics": [adapt_epic_progress(epic) for epic in metrics.epics],
        "iterations": [
            adapt_iteration_progress(iteration) for iteration in metrics.iterations
        ],
        "pull_request_linkage": adapt_pull_request_linkage(
            metrics.pull_request_linkage
        ),
        # Data-quality counters. These exist so a thin number is recognisable
        # as thin data rather than read as a result.
        "data_quality": {
            "completed_without_start": metrics.completed_without_start,
            "completed_without_transitions": metrics.completed_without_transitions,
            "active_without_state_history": metrics.active_without_state_history,
            "inconsistent_timestamps": metrics.inconsistent_timestamps,
            "open_state_unknown": metrics.open_state_unknown,
            "unestimated_completed": metrics.unestimated_completed,
            "tickets_by_workflow": metrics.tickets_by_workflow,
        },
    }
