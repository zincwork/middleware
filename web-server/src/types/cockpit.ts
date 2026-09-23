/**
 * Shapes returned by the ticket flow endpoints.
 *
 * These were declared inside the API routes in Layers 2 and 3, which was
 * fine while the routes were the only consumer. The page needs them too, so
 * they move here — matching how `@/types/resources` already serves both the
 * DORA route and its slice. A new file rather than an addition to
 * `resources.ts`, so nothing existing is disturbed.
 *
 * Durations are whole seconds throughout. Anything that could not be
 * measured is null rather than zero, which is the single most important
 * property of these shapes: a null means "no data", a zero means "measured,
 * and it was zero", and the page renders them differently.
 */

export type TicketFlowDurationStats = {
  count: number;
  mean: number | null;
  p50: number | null;
  p75: number | null;
  p95: number | null;
  min: number | null;
  max: number | null;
};

export type TicketFlowStateMetric = {
  state: string;
  state_type: string;
  /** Distinct tickets that entered this state at least once. */
  tickets: number;
  /** Total stays. Above `tickets` means work came back. */
  visits: number;
  total_seconds: number;
  /** Tickets sitting here right now. */
  open_tickets: number;
  duration: TicketFlowDurationStats;
};

export type TicketFlowEpic = {
  epic_id: string;
  total: number;
  completed: number;
  in_progress: number;
  blocked: number;
  not_started: number;
  cancelled: number;
  state_unknown: number;
  percent_complete: number | null;
  estimate_total: number | null;
  estimate_completed: number | null;
  unestimated: number;
};

export type TicketFlowIteration = {
  iteration_id: string;
  total: number;
  completed: number;
  estimate_total: number | null;
  estimate_completed: number | null;
};

export type TicketFlowLinkage = {
  tickets: number;
  tickets_with_link: number;
  links_total: number;
  links_resolved_to_pull_request: number;
  link_rate: number | null;
  resolution_rate: number | null;
};

export type TicketFlowDataQuality = {
  completed_without_start: number;
  completed_without_transitions: number;
  active_without_state_history: number;
  inconsistent_timestamps: number;
  open_state_unknown: number;
  unestimated_completed: number;
  tickets_by_workflow: Record<string, number>;
};

export type TicketFlowResponse = {
  /** True when the selected GitHub team has no Shortcut team mapped. */
  unmapped?: boolean;
  /**
   * When the point-in-time figures were taken, and the cap on the open end
   * of every state duration.
   */
  as_of: DateString | null;
  throughput: number;
  throughput_by_week: Record<DateString, number>;
  cycle_time: TicketFlowDurationStats;
  lead_time: TicketFlowDurationStats;
  queue_time: TicketFlowDurationStats;
  state_metrics: TicketFlowStateMetric[];
  blocked_time: TicketFlowDurationStats;
  completed_by_type: Record<string, number>;
  bug_ratio: number | null;
  wip: number;
  blocked_now: number;
  open_total: number;
  epics: TicketFlowEpic[];
  iterations: TicketFlowIteration[];
  pull_request_linkage: TicketFlowLinkage;
  data_quality: TicketFlowDataQuality;
};

export type TicketFlowTeamResult = {
  slug: string;
  name: string;
  unmapped: boolean;
  manager: string | null;
  metrics: TicketFlowResponse;
};

export type TicketFlowComparisonScope =
  | { kind: 'manager'; manager: string }
  | { kind: 'teams' }
  | { kind: 'empty'; reason: string };

export type TicketFlowComparisonResponse = {
  scope: TicketFlowComparisonScope;
  teams: TicketFlowTeamResult[];
  /** Null when no team in scope is mapped — a zero would read as a result. */
  combined: TicketFlowResponse | null;
  unmapped_teams: string[];
};
