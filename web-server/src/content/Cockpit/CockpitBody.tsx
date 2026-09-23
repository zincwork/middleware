import { Grid } from '@mui/material';
import { useEffect, useState } from 'react';

import { EmptyState } from '@/components/EmptyState';
import { FixedContentRefreshLoader } from '@/components/FixedContentRefreshLoader/FixedContentRefreshLoader';
import { FlexBox } from '@/components/FlexBox';
import { GithubTeamFilterNotice } from '@/components/GithubTeamSelector';
import { MiniLoader } from '@/components/MiniLoader';
import { SomethingWentWrong } from '@/components/SomethingWentWrong/SomethingWentWrong';
import { Line } from '@/components/Text';
import { FetchState } from '@/constants/ui-states';
import { hasFlowData } from '@/content/Cockpit/formatters';
import { useAuth } from '@/hooks/useAuth';
import { useSingleTeamConfig } from '@/hooks/useStateTeamConfig';
import {
  clearComparison,
  fetchTicketFlow,
  fetchTicketFlowComparison
} from '@/slices/cockpit';
import { useDispatch, useSelector } from '@/store';
import { getRandomLoadMsg } from '@/utils/loading-messages';

import { DataQualityNotes } from './DataQualityNotes';
import { EpicProgress } from './EpicProgress';
import { FlowSummary } from './FlowSummary';
import { ManagerRollup } from './ManagerRollup';
import { StateWaterfall } from './StateWaterfall';
import { useManagers } from './useManagers';

/**
 * The Cockpit view.
 *
 * Reads the same three controls as the DORA page — team, date range and the
 * GitHub team dropdown — because they all live in the shared page header and
 * the selection is in global state. Picking Skipper on the DORA page carries
 * across to here, which is the behaviour anyone would expect.
 */
export const CockpitBody = () => {
  const dispatch = useDispatch();
  const { orgId } = useAuth();
  const { dates } = useSingleTeamConfig();
  const githubTeam = useSelector((s) => s.app.githubTeamSlug);

  const flow = useSelector((s) => s.cockpit.flow);
  const comparison = useSelector((s) => s.cockpit.comparison);
  const firstLoadDone = useSelector((s) => s.cockpit.firstLoadDone);
  const isLoading = useSelector(
    (s) => s.cockpit.requests?.flow === FetchState.REQUEST
  );
  const isErrored = useSelector(
    (s) => s.cockpit.requests?.flow === FetchState.FAILURE
  );
  const isRollupLoading = useSelector(
    (s) => s.cockpit.requests?.comparison === FetchState.REQUEST
  );

  const managers = useManagers();
  const [manager, setManager] = useState('');

  useEffect(() => {
    dispatch(
      fetchTicketFlow({
        orgId,
        fromDate: dates.start,
        toDate: dates.end,
        githubTeam
      })
    );
  }, [dispatch, orgId, dates.start, dates.end, githubTeam]);

  useEffect(() => {
    if (!manager) {
      dispatch(clearComparison());
      return;
    }
    dispatch(
      fetchTicketFlowComparison({
        orgId,
        fromDate: dates.start,
        toDate: dates.end,
        manager
      })
    );
  }, [dispatch, orgId, dates.start, dates.end, manager]);

  if (isErrored)
    return (
      <SomethingWentWrong
        error="Ticket flow could not be loaded"
        desc="The Shortcut sync may not have run yet. Check the logs for the Tickets Sync step."
      />
    );

  if (!firstLoadDone) return <MiniLoader label={getRandomLoadMsg()} />;

  // An unmapped team is not an error and not an absence of work — it is a
  // team nobody has connected to Shortcut. Saying which is which matters,
  // because ten of the sixteen GitHub teams will never be mapped.
  if (flow?.unmapped)
    return (
      <FlexBox col gap2 fullWidth>
        <GithubTeamFilterNotice />
        <EmptyState
          type="NO_DATA_IN_DORA_METRICS"
          title="No Shortcut team mapped to this GitHub team"
          desc="Ticket metrics need a Shortcut team behind the GitHub team. Map one with setup_shortcut.py, or pick a different team."
        />
      </FlexBox>
    );

  if (!hasFlowData(flow))
    return (
      <FlexBox col gap2 fullWidth>
        <GithubTeamFilterNotice />
        <EmptyState
          type="NO_DATA_IN_DORA_METRICS"
          title="Nothing to show for this window"
          desc="No tickets were completed or open in the selected dates. Try a wider range, or check the Shortcut sync has run."
        />
        <FixedContentRefreshLoader show={isLoading} />
      </FlexBox>
    );

  return (
    <FlexBox col gap2 fullWidth>
      <FixedContentRefreshLoader show={isLoading} />
      <GithubTeamFilterNotice />

      <Line tiny secondary>
        Measured from Shortcut state history. DORA metrics on the other page
        start at the first commit, so everything before that — backlog, waiting
        on product, blocked — is invisible there and visible here.
      </Line>

      <FlowSummary metrics={flow} />

      <Grid container spacing={2}>
        <Grid item xs={12} lg={7}>
          <StateWaterfall states={flow.state_metrics} />
        </Grid>
        <Grid item xs={12} lg={5}>
          <EpicProgress epics={flow.epics} />
        </Grid>
      </Grid>

      <ManagerRollup
        managers={managers}
        selected={manager}
        onSelect={setManager}
        comparison={comparison}
        isLoading={isRollupLoading}
      />

      <DataQualityNotes metrics={flow} />
    </FlexBox>
  );
};
