import {
  describeDataQuality,
  durationTooltip,
  formatPercent,
  hasFlowData,
  humaniseDuration,
  isReworkHeavy,
  percentOfMax,
  reworkRatio,
  THEMES_WORKFLOW_ID
} from '@/content/Cockpit/formatters';

import type {
  TicketFlowDurationStats,
  TicketFlowResponse
} from '@/types/cockpit';

const DAY = 86400;

const noDuration: TicketFlowDurationStats = {
  count: 0,
  mean: null,
  p50: null,
  p75: null,
  p95: null,
  min: null,
  max: null
};

const duration = (
  over: Partial<TicketFlowDurationStats>
): TicketFlowDurationStats => ({ ...noDuration, ...over });

const flow = (over: Partial<TicketFlowResponse> = {}): TicketFlowResponse => ({
  as_of: '2026-09-22T00:00:00+00:00',
  throughput: 0,
  throughput_by_week: {},
  cycle_time: noDuration,
  lead_time: noDuration,
  queue_time: noDuration,
  state_metrics: [],
  blocked_time: noDuration,
  completed_by_type: {},
  bug_ratio: null,
  wip: 0,
  blocked_now: 0,
  open_total: 0,
  epics: [],
  iterations: [],
  pull_request_linkage: {
    tickets: 0,
    tickets_with_link: 0,
    links_total: 0,
    links_resolved_to_pull_request: 0,
    link_rate: null,
    resolution_rate: null
  },
  data_quality: {
    completed_without_start: 0,
    completed_without_transitions: 0,
    active_without_state_history: 0,
    inconsistent_timestamps: 0,
    open_state_unknown: 0,
    unestimated_completed: 0,
    tickets_by_workflow: {}
  },
  ...over
});

describe('humaniseDuration', () => {
  it('picks a unit a person can read', () => {
    expect(humaniseDuration(30)).toBe('<1m');
    expect(humaniseDuration(45 * 60)).toBe('45m');
    expect(humaniseDuration(3 * 3600)).toBe('3h');
    expect(humaniseDuration(6 * DAY)).toBe('6d');
  });

  it('keeps one decimal where it changes the reading', () => {
    // sc-8765's real review wait: 86795s. "1d" would hide the six minutes,
    // but rounding to "1.004d" would be noise. One decimal is the balance.
    expect(humaniseDuration(86795)).toBe('1d');
    expect(humaniseDuration(1.5 * DAY)).toBe('1.5d');
    expect(humaniseDuration(3.25 * 3600)).toBe('3.3h');
  });

  it('shows an unmeasured value as a dash, never as zero', () => {
    // This is the whole reason the backend returns null. A "0d" cycle time
    // reads as instant delivery.
    expect(humaniseDuration(null)).toBe('—');
    expect(humaniseDuration(undefined)).toBe('—');
    expect(humaniseDuration(0)).toBe('<1m');
  });
});

describe('formatPercent', () => {
  it('distinguishes a measured zero from no measurement', () => {
    expect(formatPercent(0)).toBe('0%');
    expect(formatPercent(null)).toBe('—');
    expect(formatPercent(33.3)).toBe('33.3%');
  });
});

describe('percentOfMax', () => {
  it('scales against the largest bar', () => {
    expect(percentOfMax(50, 100)).toBe(50);
    expect(percentOfMax(100, 100)).toBe(100);
  });

  it('keeps a tiny real value visible', () => {
    // An invisible bar reads as "no data". A 1% sliver reads as "very small".
    expect(percentOfMax(1, 1000000)).toBe(1);
  });

  it('returns nothing when there is no maximum', () => {
    expect(percentOfMax(5, 0)).toBe(0);
    expect(percentOfMax(5, -1)).toBe(0);
  });

  it('never exceeds the track', () => {
    expect(percentOfMax(200, 100)).toBe(100);
  });
});

describe('reworkRatio and isReworkHeavy', () => {
  it('measures visits per ticket', () => {
    expect(reworkRatio({ visits: 3, tickets: 2 })).toBe(1.5);
    expect(reworkRatio({ visits: 2, tickets: 2 })).toBe(1);
  });

  it('flags a state that work keeps coming back to', () => {
    // Half of everything that touches the state re-enters it. That is a
    // different problem from the state simply being slow, which is why it
    // gets its own signal rather than being folded into the duration.
    expect(isReworkHeavy({ visits: 3, tickets: 2 })).toBe(true);
    expect(isReworkHeavy({ visits: 10, tickets: 4 })).toBe(true);
  });

  it('does not flag ordinary flow', () => {
    expect(isReworkHeavy({ visits: 10, tickets: 10 })).toBe(false);
    expect(isReworkHeavy({ visits: 11, tickets: 10 })).toBe(false);
  });

  it('is null, not zero, with no tickets', () => {
    expect(reworkRatio({ visits: 0, tickets: 0 })).toBeNull();
    expect(isReworkHeavy({ visits: 0, tickets: 0 })).toBe(false);
  });
});

describe('hasFlowData', () => {
  it('is false for null, unmapped, and genuinely empty', () => {
    expect(hasFlowData(null)).toBe(false);
    expect(hasFlowData(flow({ unmapped: true, throughput: 5 }))).toBe(false);
    expect(hasFlowData(flow())).toBe(false);
  });

  it('is true when there is anything at all to show', () => {
    expect(hasFlowData(flow({ throughput: 1 }))).toBe(true);
    // Nothing completed, but four tickets sitting in review is exactly what
    // a manager needs to see — an empty state here would hide it.
    expect(hasFlowData(flow({ open_total: 4 }))).toBe(true);
  });
});

describe('durationTooltip', () => {
  it('shows the spread and the sample size', () => {
    expect(
      durationTooltip(
        duration({ count: 12, p50: DAY, p75: 2 * DAY, p95: 9 * DAY })
      )
    ).toBe('p50 1d · p75 2d · p95 9d · over 12 tickets');
  });

  it('says nothing was measured rather than showing dashes', () => {
    expect(durationTooltip(noDuration)).toBe(
      'No tickets measured in this window'
    );
  });

  it('gets the singular right', () => {
    expect(durationTooltip(duration({ count: 1, p50: DAY }))).toContain(
      'over 1 ticket'
    );
  });
});

describe('describeDataQuality', () => {
  it('says nothing when there is nothing to say', () => {
    expect(describeDataQuality(flow({ throughput: 10 }))).toEqual([]);
  });

  it('warns when cycle time covers too small a share of the work', () => {
    const notes = describeDataQuality(
      flow({
        throughput: 10,
        cycle_time: duration({ count: 4, p50: DAY }),
        data_quality: {
          ...flow().data_quality,
          completed_without_start: 6
        }
      })
    );
    expect(notes).toHaveLength(1);
    expect(notes[0].severity).toBe('warning');
    expect(notes[0].message).toContain('4 of 10');
  });

  it('downgrades to a note when the coverage is good', () => {
    const notes = describeDataQuality(
      flow({
        throughput: 10,
        cycle_time: duration({ count: 9, p50: DAY }),
        data_quality: { ...flow().data_quality, completed_without_start: 1 }
      })
    );
    expect(notes[0].severity).toBe('info');
  });

  it('warns about open tickets missing from both WIP and blocked', () => {
    // wip: 0 with an unclassifiable state is indistinguishable from
    // "nothing in progress" unless this is said.
    const notes = describeDataQuality(
      flow({ data_quality: { ...flow().data_quality, open_state_unknown: 3 } })
    );
    expect(notes[0].severity).toBe('warning');
    expect(notes[0].message).toContain('3 open tickets');
  });

  it('warns when the themes workflow is inflating throughput', () => {
    const notes = describeDataQuality(
      flow({
        throughput: 12,
        data_quality: {
          ...flow().data_quality,
          tickets_by_workflow: { '500000005': 9, [THEMES_WORKFLOW_ID]: 3 }
        }
      })
    );
    expect(notes).toHaveLength(1);
    expect(notes[0].severity).toBe('warning');
    expect(notes[0].message).toContain('Mid-Longterm themes');
  });

  it('ignores delivery workflows in that check', () => {
    const notes = describeDataQuality(
      flow({
        throughput: 9,
        data_quality: {
          ...flow().data_quality,
          tickets_by_workflow: { '500000005': 5, '500000847': 4 }
        }
      })
    );
    expect(notes).toEqual([]);
  });

  it('explains the absence of a velocity figure when nothing is estimated', () => {
    const notes = describeDataQuality(
      flow({
        throughput: 7,
        data_quality: { ...flow().data_quality, unestimated_completed: 7 }
      })
    );
    expect(notes[0].message).toContain('no velocity figure');
  });

  it('stays quiet when only some tickets lack an estimate', () => {
    expect(
      describeDataQuality(
        flow({
          throughput: 7,
          data_quality: { ...flow().data_quality, unestimated_completed: 3 }
        })
      )
    ).toEqual([]);
  });

  it('gets singulars right across every note', () => {
    const notes = describeDataQuality(
      flow({
        throughput: 2,
        cycle_time: duration({ count: 1, p50: DAY }),
        data_quality: {
          ...flow().data_quality,
          completed_without_start: 1,
          open_state_unknown: 1,
          active_without_state_history: 1,
          inconsistent_timestamps: 1
        }
      })
    );
    const joined = notes.map((n) => n.message).join(' ');
    expect(joined).toContain('1 open ticket has a state');
    expect(joined).toContain('1 open ticket contributed');
    expect(joined).toContain('1 ticket has provider timestamps');
    expect(joined).not.toContain('1 tickets');
  });
});
