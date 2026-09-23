import { Box, Chip, Tooltip, useTheme } from '@mui/material';
import { FC, useMemo } from 'react';

import { FlexBox } from '@/components/FlexBox';
import { Line } from '@/components/Text';
import {
  durationTooltip,
  humaniseDuration,
  isReworkHeavy,
  percentOfMax,
  reworkRatio
} from '@/content/Cockpit/formatters';

import { CockpitSection } from './CockpitSection';

import type { TicketFlowStateMetric } from '@/types/cockpit';

/**
 * Where the time goes, per workflow state.
 *
 * Plain bars sized by percentage rather than a charting library. For six to
 * nine states this is the whole chart, and it inherits theme colours and
 * responsive width for nothing — a nivo BarChart would need a theme mapping
 * and an explicit height to do the same job.
 *
 * The rework chip is the part worth looking at. `visits` above `tickets`
 * means work came back: a state entered 1.5 times per ticket or more is
 * being re-entered by half of everything that touches it, which is a
 * different problem from the state simply being slow.
 */
export const StateWaterfall: FC<{ states: TicketFlowStateMetric[] }> = ({
  states
}) => {
  const theme = useTheme();
  const max = useMemo(
    () => Math.max(0, ...states.map((state) => state.total_seconds)),
    [states]
  );

  if (!states.length)
    return (
      <CockpitSection title="Where the time goes">
        <Line secondary>
          No state history in this window. Either the tickets predate the
          sync, or nothing moved.
        </Line>
      </CockpitSection>
    );

  return (
    <CockpitSection
      title="Where the time goes"
      subtitle="Total time in each state, clipped to the selected window. Repeat visits are summed."
    >
      <FlexBox col gap1 fullWidth>
        {states.map((state) => {
          const rework = isReworkHeavy(state);
          return (
            <FlexBox key={state.state} col gap={'2px'} fullWidth>
              <FlexBox justifyBetween alignCenter gap1 fullWidth>
                <FlexBox alignCenter gap1 minWidth={0}>
                  <Line medium noWrap>
                    {state.state}
                  </Line>
                  {rework ? (
                    <Tooltip
                      arrow
                      title={`Entered ${reworkRatio(state)} times per ticket — work is coming back to this state`}
                    >
                      <Chip
                        size="small"
                        color="warning"
                        label={`rework ${reworkRatio(state)}x`}
                      />
                    </Tooltip>
                  ) : null}
                </FlexBox>
                <Tooltip arrow title={durationTooltip(state.duration)}>
                  <Line medium noWrap>
                    {humaniseDuration(state.duration.p50)}
                    <Line tiny secondary component="span">
                      {' '}
                      typical
                    </Line>
                  </Line>
                </Tooltip>
              </FlexBox>

              <Box
                width="100%"
                height="8px"
                borderRadius="4px"
                bgcolor={theme.colors.secondary.lighter}
                overflow="hidden"
              >
                <Box
                  width={`${percentOfMax(state.total_seconds, max)}%`}
                  height="100%"
                  bgcolor={
                    rework
                      ? theme.colors.warning.main
                      : theme.colors.primary.main
                  }
                />
              </Box>

              <Line tiny secondary>
                {state.tickets} ticket{state.tickets === 1 ? '' : 's'} ·{' '}
                {state.visits} visit{state.visits === 1 ? '' : 's'} ·{' '}
                {humaniseDuration(state.total_seconds)} in total
                {state.open_tickets
                  ? ` · ${state.open_tickets} sitting here now`
                  : ''}
              </Line>
            </FlexBox>
          );
        })}
      </FlexBox>
    </CockpitSection>
  );
};
