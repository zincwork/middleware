/**
 * Cockpit state: ticket flow for the selected team, and the manager roll-up.
 *
 * A slice rather than local component state, for two reasons: the page
 * wrapper reads its loading flag from the store the way every other page
 * does, and the two requests need independent loading states so the roll-up
 * table can still be loading while the summary strip is already rendered.
 */

import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

import { handleApi } from '@/api-helpers/axios-api-instance';
import { StateFetchConfig } from '@/types/redux';
import {
  TicketFlowComparisonResponse,
  TicketFlowResponse
} from '@/types/cockpit';
import { addFetchCasesToReducer } from '@/utils/redux';

export type State = StateFetchConfig<{
  firstLoadDone: boolean;
  flow: TicketFlowResponse | null;
  comparison: TicketFlowComparisonResponse | null;
}>;

const initialState: State = {
  firstLoadDone: false,
  flow: null,
  comparison: null
};

export const fetchTicketFlow = createAsyncThunk(
  'cockpit/fetchTicketFlow',
  async (params: {
    orgId: ID;
    fromDate: Date;
    toDate: Date;
    githubTeam?: string | null;
  }) => {
    return await handleApi<TicketFlowResponse>(
      `internal/${params.orgId}/ticket_flow`,
      {
        params: {
          from_date: params.fromDate,
          to_date: params.toDate,
          github_team: params.githubTeam || null
        }
      }
    );
  }
);

export const fetchTicketFlowComparison = createAsyncThunk(
  'cockpit/fetchTicketFlowComparison',
  async (params: {
    orgId: ID;
    fromDate: Date;
    toDate: Date;
    manager: string;
  }) => {
    return await handleApi<TicketFlowComparisonResponse>(
      `internal/${params.orgId}/ticket_flow_comparison`,
      {
        params: {
          from_date: params.fromDate,
          to_date: params.toDate,
          manager: params.manager
        }
      }
    );
  }
);

export const cockpitSlice = createSlice({
  name: 'cockpit',
  initialState,
  reducers: {
    // The roll-up is cleared when the manager selection is cleared, so the
    // previous manager's numbers cannot linger under a new heading.
    clearComparison(state: State) {
      state.comparison = null;
    }
  },
  extraReducers: (builder) => {
    addFetchCasesToReducer(builder, fetchTicketFlow, 'flow', (state, action) => {
      state.firstLoadDone = true;
      state.flow = action.payload;
    });
    addFetchCasesToReducer(
      builder,
      fetchTicketFlowComparison,
      'comparison',
      (state, action) => {
        state.comparison = action.payload;
      }
    );
  }
});

export const { clearComparison } = cockpitSlice.actions;
