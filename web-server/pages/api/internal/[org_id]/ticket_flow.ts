/**
 * Ticket flow metrics for the selected GitHub team.
 *
 * The whole point of this route is the one translation it performs: the UI
 * knows about GitHub teams (a group of people), the analytics server knows
 * about Shortcut team ids. github_teams.json holds the mapping, it is only
 * readable server-side, and `getShortcutTeamIds` is the accessor. Doing the
 * lookup here means the Python layer never learns that GitHub exists, and
 * re-mapping a squad stays a config edit.
 *
 * `github_team` and `attribution` are the same query params the DORA route
 * already takes, so the dropdown drives both halves of the page unchanged.
 */

import { endOfDay, startOfDay } from 'date-fns';
import { isNil, reject } from 'ramda';
import * as yup from 'yup';

import { handleRequest } from '@/api-helpers/axios';
import { Endpoint } from '@/api-helpers/global';
import { isoDateString } from '@/utils/date';
import { ALL_GITHUB_TEAMS, getShortcutTeamIds } from '@/utils/githubTeams';

// Declared in @/types/cockpit rather than here, because the Cockpit page
// needs the same shapes. Same arrangement as the DORA route and its slice,
// which both read from @/types/resources.
import type {
  TicketFlowDurationStats,
  TicketFlowResponse
} from '@/types/cockpit';

const pathSchema = yup.object().shape({
  org_id: yup.string().uuid().required()
});

const getSchema = yup.object().shape({
  from_date: yup.date().required(),
  to_date: yup.date().required(),
  github_team: yup.string().optional().nullable(),
  ticket_types: yup.array().of(yup.string()).optional().nullable(),
  epic_ids: yup.array().of(yup.string()).optional().nullable(),
  include_subtasks: yup.boolean().optional()
});

const endpoint = new Endpoint(pathSchema);

endpoint.handle.GET(getSchema, async (req, res) => {
  const {
    org_id,
    from_date,
    to_date,
    github_team,
    ticket_types,
    epic_ids,
    include_subtasks
  } = req.payload;

  return res.send(
    await getTicketFlowMetrics({
      org_id,
      from_date,
      to_date,
      github_team,
      ticket_types,
      epic_ids,
      include_subtasks
    })
  );
});

const NO_DURATION: TicketFlowDurationStats = {
  count: 0,
  mean: null,
  p50: null,
  p75: null,
  p95: null,
  min: null,
  max: null
};

/**
 * An empty result rather than an error when the selected team has no
 * Shortcut mapping.
 *
 * Ten of Zinc's sixteen GitHub teams are unmapped and some of them never
 * will be — a 500 on "Marketing" would be a bug report, not information.
 * `unmapped: true` lets the UI say why the view is empty rather than showing
 * a convincing row of zeroes.
 *
 * Spelled out in full, with the same keys as a real response, so callers
 * handle one shape instead of a union.
 */
export const EMPTY_TICKET_FLOW: TicketFlowResponse = {
  unmapped: true,
  as_of: null,
  throughput: 0,
  throughput_by_week: {},
  cycle_time: NO_DURATION,
  lead_time: NO_DURATION,
  queue_time: NO_DURATION,
  state_metrics: [],
  blocked_time: NO_DURATION,
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
  }
};

export type TicketFlowQuery = {
  org_id: ID;
  from_date: DateString | Date;
  to_date: DateString | Date;
  ticket_types?: string[] | null;
  epic_ids?: string[] | null;
  include_subtasks?: boolean;
};

/**
 * The one place that talks to the analytics server about ticket flow.
 *
 * Takes Shortcut team ids rather than a GitHub team slug, so the roll-up can
 * ask for the union of several teams in a single query. That matters more
 * than it looks: a combined figure has to be computed from the underlying
 * tickets, because averaging each team's average lead time weights a
 * three-ticket team the same as a thirty-ticket one and is simply the wrong
 * number. Passing the union down means the weighting is right by
 * construction rather than by arithmetic up here.
 *
 * An empty `provider_team_ids` means "do not filter by team", i.e. the whole
 * org. Callers that mean "this team, which happens to be unmapped" must
 * check for that themselves — see getTicketFlowMetrics.
 */
export const fetchTicketFlowForTeamIds = async (
  params: TicketFlowQuery & { provider_team_ids: string[] }
) => {
  const ticket_filter = reject(isNil, {
    provider_team_ids: params.provider_team_ids.length
      ? params.provider_team_ids
      : null,
    ticket_types: params.ticket_types?.length ? params.ticket_types : null,
    epic_ids: params.epic_ids?.length ? params.epic_ids : null,
    include_subtasks: params.include_subtasks ?? null
  });

  return handleRequest<TicketFlowResponse>(
    `/orgs/${params.org_id}/ticket_flow`,
    {
      params: {
        from_time: isoDateString(startOfDay(new Date(params.from_date))),
        to_time: isoDateString(endOfDay(new Date(params.to_date))),
        ...(Object.keys(ticket_filter).length
          ? { ticket_filter: JSON.stringify(ticket_filter) }
          : {})
      }
    }
  );
};

export const getTicketFlowMetrics = async (
  params: TicketFlowQuery & { github_team?: string | null }
) => {
  const provider_team_ids = getShortcutTeamIds(params.github_team);

  // "All" deliberately still returns data: the org-wide ticket flow is
  // meaningful, unlike an unmapped single team.
  const isAll =
    !params.github_team || params.github_team === ALL_GITHUB_TEAMS;

  if (!isAll && !provider_team_ids.length) {
    return EMPTY_TICKET_FLOW;
  }

  return fetchTicketFlowForTeamIds({ ...params, provider_team_ids });
};

export default endpoint.serve();
