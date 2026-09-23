import {
  MenuItem,
  Select,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip
} from '@mui/material';
import { FC } from 'react';

import { FlexBox } from '@/components/FlexBox';
import { MiniLoader } from '@/components/MiniLoader';
import { Line } from '@/components/Text';
import {
  durationTooltip,
  formatPercent,
  humaniseDuration
} from '@/content/Cockpit/formatters';

import { CockpitSection } from './CockpitSection';

import type { TicketFlowComparisonResponse } from '@/types/cockpit';

/**
 * Teams under one manager, side by side, with a combined row.
 *
 * The combined row is NOT derived from the rows above it. It comes back from
 * the server as a single query over the union of the teams' Shortcut ids,
 * because averaging per-team medians weights a three-ticket team the same as
 * a thirty-ticket one. On Zinc's own data that turns a 1-day median into
 * 50.5 days. There is a test asserting the two disagree; this component just
 * renders what it is given and does no arithmetic of its own.
 */
export const ManagerRollup: FC<{
  managers: string[];
  selected: string;
  onSelect: (manager: string) => void;
  comparison: TicketFlowComparisonResponse | null;
  isLoading: boolean;
}> = ({ managers, selected, onSelect, comparison, isLoading }) => {
  if (!managers.length)
    return (
      <CockpitSection title="By manager">
        <Line secondary>
          No GitHub team has a manager set. Run set_managers.py to enable the
          roll-up.
        </Line>
      </CockpitSection>
    );

  const scope = comparison?.scope;

  return (
    <CockpitSection
      title="By manager"
      subtitle="Teams side by side. The combined row is computed from the underlying tickets, not averaged from the rows above."
    >
      <FlexBox gap1 alignCenter>
        <Line tiny secondary>
          Manager
        </Line>
        <Select
          size="small"
          value={selected}
          onChange={(event) => onSelect(event.target.value)}
          displayEmpty
        >
          <MenuItem value="">
            <Line secondary>None</Line>
          </MenuItem>
          {managers.map((manager) => (
            <MenuItem key={manager} value={manager}>
              {manager}
            </MenuItem>
          ))}
        </Select>
      </FlexBox>

      {!selected ? (
        <Line secondary>Pick a manager to see their teams together.</Line>
      ) : isLoading ? (
        <MiniLoader label="Adding up the teams" />
      ) : scope?.kind === 'empty' ? (
        <Line warning>{scope.reason}</Line>
      ) : !comparison?.teams.length ? (
        <Line secondary>Nothing to show for this manager.</Line>
      ) : (
        <>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Team</TableCell>
                <TableCell align="right">Throughput</TableCell>
                <TableCell align="right">Cycle time</TableCell>
                <TableCell align="right">Lead time</TableCell>
                <TableCell align="right">Bugs</TableCell>
                <TableCell align="right">In progress</TableCell>
                <TableCell align="right">Blocked</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {comparison.teams.map((team) => (
                <TableRow key={team.slug}>
                  <TableCell>{team.name}</TableCell>
                  {team.unmapped ? (
                    <TableCell colSpan={6}>
                      <Line tiny secondary>
                        No Shortcut team mapped — not measured
                      </Line>
                    </TableCell>
                  ) : (
                    <>
                      <TableCell align="right">
                        {team.metrics.throughput}
                      </TableCell>
                      <TableCell align="right">
                        <Tooltip
                          arrow
                          title={durationTooltip(team.metrics.cycle_time)}
                        >
                          <span>
                            {humaniseDuration(team.metrics.cycle_time.p50)}
                          </span>
                        </Tooltip>
                      </TableCell>
                      <TableCell align="right">
                        {humaniseDuration(team.metrics.lead_time.p50)}
                      </TableCell>
                      <TableCell align="right">
                        {formatPercent(team.metrics.bug_ratio)}
                      </TableCell>
                      <TableCell align="right">{team.metrics.wip}</TableCell>
                      <TableCell align="right">
                        {team.metrics.blocked_now}
                      </TableCell>
                    </>
                  )}
                </TableRow>
              ))}

              {comparison.combined ? (
                <TableRow>
                  <TableCell>
                    <Line medium>Combined</Line>
                  </TableCell>
                  <TableCell align="right">
                    <Line medium>{comparison.combined.throughput}</Line>
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip
                      arrow
                      title={durationTooltip(comparison.combined.cycle_time)}
                    >
                      <span>
                        <Line medium>
                          {humaniseDuration(
                            comparison.combined.cycle_time.p50
                          )}
                        </Line>
                      </span>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">
                    <Line medium>
                      {humaniseDuration(comparison.combined.lead_time.p50)}
                    </Line>
                  </TableCell>
                  <TableCell align="right">
                    <Line medium>
                      {formatPercent(comparison.combined.bug_ratio)}
                    </Line>
                  </TableCell>
                  <TableCell align="right">
                    <Line medium>{comparison.combined.wip}</Line>
                  </TableCell>
                  <TableCell align="right">
                    <Line medium>{comparison.combined.blocked_now}</Line>
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>

          {comparison.unmapped_teams.length ? (
            <Line tiny warning>
              Not included in the combined row:{' '}
              {comparison.unmapped_teams.join(', ')}. Map them with
              setup_shortcut.py, or this is only part of the area.
            </Line>
          ) : null}
        </>
      )}
    </CockpitSection>
  );
};
