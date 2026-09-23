/**
 * Pure formatting and interpretation for the Cockpit view.
 *
 * Kept out of the components on purpose: these are the functions that decide
 * whether a number gets shown as a result or flagged as thin data, and that
 * judgement is worth testing without mounting React.
 */

import type {
  TicketFlowDurationStats,
  TicketFlowResponse,
  TicketFlowStateMetric
} from '@/types/cockpit';

/**
 * Zinc's "Mid-Longterm themes" workflow. Its states are Captured ->
 * Investigation -> Aligned, and its records are themes rather than delivery
 * work, so anything from it inflates throughput. Not filtered out
 * automatically — the point is to show it rather than hide it.
 */
export const THEMES_WORKFLOW_ID = '500000529';

/** A state entered noticeably more often than it has tickets is rework. */
export const REWORK_THRESHOLD = 1.5;

/** Below this, a cycle time figure describes too small a subset to quote. */
export const THIN_SAMPLE_RATIO = 0.8;

export const humaniseDuration = (seconds?: number | null): string => {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 60) return '<1m';
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${round1(seconds / 3600)}h`;
  return `${round1(seconds / 86400)}d`;
};

export const formatPercent = (value?: number | null): string =>
  value === null || value === undefined ? '—' : `${value}%`;

export const formatCount = (value?: number | null): string =>
  value === null || value === undefined ? '—' : String(value);

const round1 = (value: number) => Math.round(value * 10) / 10;

/** Bar width as a percentage of the largest value in the set. */
export const percentOfMax = (value: number, max: number): number => {
  if (!max || max <= 0) return 0;
  // Floored at 1% so a real but tiny value is still a visible sliver rather
  // than nothing at all — an invisible bar reads as "no data".
  return Math.max(1, Math.min(100, (value / max) * 100));
};

export const reworkRatio = (
  metric: Pick<TicketFlowStateMetric, 'visits' | 'tickets'>
): number | null => {
  if (!metric.tickets) return null;
  return round1(metric.visits / metric.tickets);
};

export const isReworkHeavy = (
  metric: Pick<TicketFlowStateMetric, 'visits' | 'tickets'>
): boolean => {
  const ratio = reworkRatio(metric);
  return ratio !== null && ratio >= REWORK_THRESHOLD;
};

export const hasFlowData = (metrics?: TicketFlowResponse | null): boolean =>
  Boolean(
    metrics &&
      !metrics.unmapped &&
      (metrics.throughput > 0 ||
        metrics.open_total > 0 ||
        metrics.state_metrics.length > 0)
  );

export const durationTooltip = (stats: TicketFlowDurationStats): string => {
  if (!stats.count) return 'No tickets measured in this window';
  return [
    `p50 ${humaniseDuration(stats.p50)}`,
    `p75 ${humaniseDuration(stats.p75)}`,
    `p95 ${humaniseDuration(stats.p95)}`,
    `over ${stats.count} ticket${stats.count === 1 ? '' : 's'}`
  ].join(' · ');
};

export type QualityNote = {
  severity: 'warning' | 'info';
  message: string;
};

/**
 * Turn the data-quality counters into things worth saying.
 *
 * The counters exist so a thin number is not read as a result. That only
 * works if someone is told, so this decides which of them are worth a line
 * on the page and which are noise at their current size.
 */
export const describeDataQuality = (
  metrics: TicketFlowResponse
): QualityNote[] => {
  const notes: QualityNote[] = [];
  const dq = metrics.data_quality;
  const measured = metrics.cycle_time.count;

  if (metrics.throughput > 0 && dq.completed_without_start > 0) {
    const ratio = measured / metrics.throughput;
    notes.push({
      severity: ratio < THIN_SAMPLE_RATIO ? 'warning' : 'info',
      message: `Cycle time covers ${measured} of ${metrics.throughput} completed tickets — ${dq.completed_without_start} were closed without ever being moved into a started state.`
    });
  }

  if (dq.open_state_unknown > 0) {
    notes.push({
      severity: 'warning',
      message: `${dq.open_state_unknown} open ticket${
        dq.open_state_unknown === 1 ? ' has a state' : 's have states'
      } the sync could not classify, so they are in neither the in-progress nor the blocked count.`
    });
  }

  if (dq.active_without_state_history > 0) {
    notes.push({
      severity: 'info',
      message: `${dq.active_without_state_history} open ticket${
        dq.active_without_state_history === 1 ? '' : 's'
      } contributed nothing to the state breakdown below — no history in this window.`
    });
  }

  if (dq.inconsistent_timestamps > 0) {
    notes.push({
      severity: 'info',
      message: `${dq.inconsistent_timestamps} ticket${
        dq.inconsistent_timestamps === 1 ? ' has' : 's have'
      } provider timestamps that contradict each other, and were left out of the affected averages rather than counted as zero.`
    });
  }

  const themes = dq.tickets_by_workflow?.[THEMES_WORKFLOW_ID];
  if (themes) {
    notes.push({
      severity: 'warning',
      message: `${themes} of the completed tickets are in the "Mid-Longterm themes" workflow, which holds themes rather than delivery work. Throughput above includes them.`
    });
  }

  if (
    metrics.throughput > 0 &&
    dq.unestimated_completed === metrics.throughput
  ) {
    notes.push({
      severity: 'info',
      message:
        'None of the completed tickets carry an estimate, which is why there is no velocity figure here.'
    });
  }

  return notes;
};
