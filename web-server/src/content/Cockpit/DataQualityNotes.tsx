import { Alert } from '@mui/material';
import { FC, useMemo } from 'react';

import { FlexBox } from '@/components/FlexBox';
import { Line } from '@/components/Text';
import { describeDataQuality } from '@/content/Cockpit/formatters';

import { CockpitSection } from './CockpitSection';

import type { TicketFlowResponse } from '@/types/cockpit';

/**
 * What the numbers above do not cover.
 *
 * The backend counts every ticket it had to leave out of a figure rather
 * than silently excluding it, and this is where those counts get said out
 * loud. A thin number that looks like a result is worse than no number, and
 * the counters only prevent that if someone reads them.
 *
 * Also the ticket-to-PR link rate, which is not a delivery metric but the
 * thing that says whether tying this view to deployments would stand up.
 */
export const DataQualityNotes: FC<{ metrics: TicketFlowResponse }> = ({
  metrics
}) => {
  const notes = useMemo(() => describeDataQuality(metrics), [metrics]);
  const linkage = metrics.pull_request_linkage;

  if (!notes.length && !linkage.tickets) return null;

  return (
    <CockpitSection title="Caveats and coverage">
      <FlexBox col gap1 fullWidth>
        {notes.map((note) => (
          <Alert key={note.message} severity={note.severity}>
            {note.message}
          </Alert>
        ))}

        {linkage.tickets ? (
          <Line tiny secondary>
            {linkage.tickets_with_link} of {linkage.tickets} completed tickets
            have a pull request recorded against them (
            {formatRate(linkage.link_rate)}), and{' '}
            {linkage.links_resolved_to_pull_request} of {linkage.links_total}{' '}
            of those links matched a pull request Middleware has synced (
            {formatRate(linkage.resolution_rate)}). A low first figure means
            the branch naming convention is not being followed; a low second
            means a repo has not been synced.
          </Line>
        ) : null}
      </FlexBox>
    </CockpitSection>
  );
};

const formatRate = (value: number | null) =>
  value === null ? 'not measurable' : `${value}%`;
