import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow
} from '@mui/material';
import { FC } from 'react';

import { Line } from '@/components/Text';
import { formatPercent } from '@/content/Cockpit/formatters';

import { CockpitSection } from './CockpitSection';

import type { TicketFlowEpic } from '@/types/cockpit';

/**
 * Epic progress: only the epics this team touched in the window, but with
 * all-time counts inside each, because "38 of 48" is a completeness question
 * rather than a rate.
 *
 * Cancelled is its own column and stays out of the total. Abandoned work is
 * not remaining work, so folding it in would make an epic look permanently
 * behind.
 *
 * Epic ids rather than names: Layer 1 stores the id, not the title. Fixing
 * that is one extra Shortcut call in the sync.
 */
export const EpicProgress: FC<{ epics: TicketFlowEpic[] }> = ({ epics }) => {
  if (!epics.length)
    return (
      <CockpitSection title="Epics">
        <Line secondary>No epic has been worked on in this window.</Line>
      </CockpitSection>
    );

  return (
    <CockpitSection
      title="Epics"
      subtitle="Epics touched in this window, with all-time progress. Cancelled work is excluded from the total."
    >
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Epic</TableCell>
            <TableCell align="right">Done</TableCell>
            <TableCell align="right">Total</TableCell>
            <TableCell align="right">In progress</TableCell>
            <TableCell align="right">Blocked</TableCell>
            <TableCell align="right">Cancelled</TableCell>
            <TableCell align="right">Complete</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {epics.map((epic) => (
            <TableRow key={epic.epic_id}>
              <TableCell>
                <Line mono tiny>
                  {epic.epic_id}
                </Line>
              </TableCell>
              <TableCell align="right">{epic.completed}</TableCell>
              <TableCell align="right">{epic.total}</TableCell>
              <TableCell align="right">{epic.in_progress}</TableCell>
              <TableCell align="right">
                {epic.blocked ? (
                  <Line warning medium>
                    {epic.blocked}
                  </Line>
                ) : (
                  0
                )}
              </TableCell>
              <TableCell align="right">
                <Line secondary>{epic.cancelled}</Line>
              </TableCell>
              <TableCell align="right">
                <Line medium>{formatPercent(epic.percent_complete)}</Line>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </CockpitSection>
  );
};
