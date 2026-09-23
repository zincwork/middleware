import { Endpoint, nullSchema } from '@/api-helpers/global';
import { getGithubTeams } from '@/utils/githubTeams';

const endpoint = new Endpoint(nullSchema);

/**
 * Lists the GitHub teams available as a metrics filter, for the header
 * dropdown and the Cockpit manager roll-up.
 *
 * Member logins are deliberately not returned — the dropdown only needs a
 * label and a count, and the author filter is resolved server-side in the
 * dora_metrics route so the list never has to travel to the browser.
 *
 * `manager` and `has_shortcut_team` are returned because the Cockpit page
 * builds its manager list from them and needs to know which teams can be
 * measured at all. Neither reveals anything the person cannot already see in
 * the team picker.
 */
endpoint.handle.GET(nullSchema, async (_req, res) => {
  const teams = getGithubTeams();

  res.send({
    teams: teams.map((team) => ({
      slug: team.slug,
      name: team.name,
      member_count: team.members.length,
      manager: team.manager ?? null,
      has_shortcut_team: Boolean(team.shortcut_team_id)
    }))
  });
});

export default endpoint.serve();
