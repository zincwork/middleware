import { Tooltip } from '@mui/material';
import { FC } from 'react';

import { FlexBox } from '@/components/FlexBox';
import { Line } from '@/components/Text';
import {
  durationTooltip,
  formatPercent,
  humaniseDuration
} from '@/content/Cockpit/formatters';

import { CockpitSection } from './CockpitSection';

import type { TicketFlowResponse } from '@/types/cockpit';

const Metric: FC<{
  label: string;
  value: string;
  hint?: string;
  tooltip?: string;
}> = ({ label, value, hint, tooltip }) => {
  const body = (
    <FlexBox col gap={'2px'} minWidth={'8em'}>
      <Line tiny secondary>
        {label}
      </Line>
      <Line huge medium>
        {value}
      </Line>
      <Line tiny secondary>
        {hint ?? ' '}
      </Line>
    </FlexBox>
  );
  return tooltip ? (
    <Tooltip title={tooltip} arrow>
      {body}
    </Tooltip>
  ) : (
    body
  );
};

/**
 * The strip along the top: the five figures worth reading first.
 *
 * Medians rather than means throughout. Cycle times are right-skewed — one
 * story stuck for six weeks moves a mean and tells you nothing about the
 * typical case — so the headline is p50 and the spread is on the tooltip.
 */
export const FlowSummary: FC<{ metrics: TicketFlowResponse }> = ({
  metrics
}) => (
  <CockpitSection
    title="Flow"
    subtitle={
      metrics.as_of
        ? `In-progress and blocked counts as of ${new Date(
            metrics.as_of
          ).toLocaleString()}`
        : undefined
    }
  >
    <FlexBox gap2 flexWrap={'wrap'}>
      <Metric
        label="Lead time"
        value={humaniseDuration(metrics.lead_time.p50)}
        hint="created to done"
        tooltip={durationTooltip(metrics.lead_time)}
      />
      <Metric
        label="Cycle time"
        value={humaniseDuration(metrics.cycle_time.p50)}
        hint="started to done"
        tooltip={durationTooltip(metrics.cycle_time)}
      />
      <Metric
        label="Waiting to start"
        value={humaniseDuration(metrics.queue_time.p50)}
        hint="the part DORA cannot see"
        tooltip={durationTooltip(metrics.queue_time)}
      />
      <Metric
        label="Throughput"
        value={String(metrics.throughput)}
        hint="tickets completed"
      />
      <Metric
        label="Bugs"
        value={formatPercent(metrics.bug_ratio)}
        hint="of completed work"
      />
      <Metric
        label="In progress"
        value={String(metrics.wip)}
        hint={`${metrics.open_total} open in total`}
      />
      <Metric
        label="Blocked"
        value={String(metrics.blocked_now)}
        hint={
          metrics.blocked_time.count
            ? `${humaniseDuration(metrics.blocked_time.p50)} typical`
            : 'none blocked in window'
        }
        tooltip={
          'Zinc types Blocked as an unstarted state, so blocked work is not counted in "In progress".'
        }
      />
    </FlexBox>
  </CockpitSection>
);
