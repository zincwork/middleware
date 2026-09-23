/**
 * Ticket flow across several teams at once: side by side, plus one combined
 * figure.
 *
 * Two ways to ask:
 *
 *   ?manager=Lucila%20Sanjurjo     -> every team she holds
 *   ?github_teams=skipper,red-team -> exactly those teams
 *
 * The manager form is the one that needed the org chart, because GitHub has
 * no notion of a manager and one manager can hold several teams — Lucila
 * holds both Bliss and Integration. It is read from the `manager` field in
 * github_teams.json, set by set_managers.py.
 *
 * THE IMPORTANT BIT: `combined` is a single query over the union of the
 * teams' Shortcut ids. It is NOT an average of the per-team figures.
 * Averaging averages weights a three-ticket team the same as a thirty-ticket
 * one, so a combined p50 built that way is not the median of anything. There
 * is a test asserting the two methods give different answers, so nobody
 * "simplifies" this back into a mean later.
 *
 * Cost is N+1 calls to the analytics server: one per team, one for the
 * union. For Zinc's largest roll-up that is three.
 */

import { isNil, reject } from 'ramda';
import * as yup from 'yup';

import { Endpoint } from '@/api-helpers/global';
import {
  getGithubTeams,
  getTeamsForManager,
  resolveShortcutTeamIdsForSlugs,
  type GithubTeam
} from '@/utils/githubTeams';

import {
  EMPTY_TICKET_FLOW,
  fetchTicketFlowForTeamIds
} from './ticket_flow';

import type {
  TicketFlowComparisonResponse,
  TicketFlowTeamResult
} from '@/types/cockpit';

const pathSchema = yup.object().shape({
  org_id: yup.string().uuid().required()
});

const getSchema = yup.object().shape({
  from_date: yup.date().required(),
  to_date: yup.date().required(),
  manager: yup.string().optional().nullable(),
  github_teams: yup.string().optional().nullable(),
  ticket_types: yup.array().of(yup.string()).optional().nullable(),
  include_subtasks: yup.boolean().optional()
});

const endpoint = new Endpoint(pathSchema);

const parseSlugs = (raw?: string | null): string[] =>
  (raw ?? '')
    .split(',')
    .map((slug) => slug.trim())
    .filter(Boolean);

endpoint.handle.GET(getSchema, async (req, res) => {
  const {
    org_id,
    from_date,
    to_date,
    manager,
    github_teams,
    ticket_types,
    include_subtasks
  } = req.payload;

  return res.send(
    await getTicketFlowComparison({
      org_id,
      from_date,
      to_date,
      manager,
      github_teams: parseSlugs(github_teams),
      ticket_types,
      include_subtasks
    })
  );
});

export const getTicketFlowComparison = async (params: {
  org_id: ID;
  from_date: DateString | Date;
  to_date: DateString | Date;
  manager?: string | null;
  github_teams?: string[];
  ticket_types?: string[] | null;
  include_subtasks?: boolean;
}): Promise<TicketFlowComparisonResponse> => {
  const requestedSlugs = params.github_teams ?? [];

  let scope: TicketFlowComparisonResponse['scope'];
  let teams: GithubTeam[];

  if (params.manager?.trim()) {
    teams = getTeamsForManager(params.manager);
    scope = teams.length
      ? { kind: 'manager', manager: params.manager.trim() }
      : {
          kind: 'empty',
          reason: `No GitHub team has "${params.manager.trim()}" as its manager. Set one with set_managers.py.`
        };
  } else if (requestedSlugs.length) {
    const all = getGithubTeams();
    teams = all.filter((team) => requestedSlugs.includes(team.slug));
    const missing = requestedSlugs.filter(
      (slug) => !all.some((team) => team.slug === slug)
    );
    scope = teams.length
      ? { kind: 'teams' }
      : {
          kind: 'empty',
          reason: `None of these are GitHub teams: ${missing.join(', ')}`
        };
  } else {
    teams = [];
    scope = {
      kind: 'empty',
      reason: 'Pass either ?manager= or ?github_teams=a,b'
    };
  }

  if (!teams.length) {
    return { scope, teams: [], combined: null, unmapped_teams: [] };
  }

  const mapped = teams.filter((team) => Boolean(team.shortcut_team_id));
  const unmapped_teams = teams
    .filter((team) => !team.shortcut_team_id)
    .map((team) => team.slug);

  const query = reject(isNil, {
    org_id: params.org_id,
    from_date: params.from_date,
    to_date: params.to_date,
    ticket_types: params.ticket_types?.length ? params.ticket_types : null,
    include_subtasks: params.include_subtasks ?? null
  }) as Parameters<typeof fetchTicketFlowForTeamIds>[0];

  const unionIds = resolveShortcutTeamIdsForSlugs(
    teams,
    mapped.map((team) => team.slug)
  );

  // Per-team in parallel, then the union. The union is the figure anyone
  // will quote, so it is computed from the tickets rather than from the rows
  // above it.
  const [perTeam, combined] = await Promise.all([
    Promise.all(
      teams.map(async (team): Promise<TicketFlowTeamResult> => {
        const ids = team.shortcut_team_id ? [team.shortcut_team_id] : [];
        return {
          slug: team.slug,
          name: team.name,
          manager: team.manager ?? null,
          unmapped: !ids.length,
          metrics: ids.length
            ? await fetchTicketFlowForTeamIds({
                ...query,
                provider_team_ids: ids
              })
            : EMPTY_TICKET_FLOW
        };
      })
    ),
    unionIds.length
      ? fetchTicketFlowForTeamIds({ ...query, provider_team_ids: unionIds })
      : Promise.resolve(null)
  ]);

  return { scope, teams: perTeam, combined, unmapped_teams };
};

export default endpoint.serve();
