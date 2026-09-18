import * as yup from 'yup';

import { Endpoint, nullSchema } from '@/api-helpers/global';
import { getGithubTeams } from '@/utils/githubTeams';

const getSchema = yup.object().shape({});

const endpoint = new Endpoint(nullSchema);

/**
 * Lists the GitHub teams available as a metrics filter, for the header
 * dropdown. Member logins are deliberately not returned — the dropdown only
 * needs a label and a count, and the author filter is resolved server-side in
 * the dora_metrics route so the list never has to travel to the browser.
 */
endpoint.handle.GET(getSchema, async (_req, res) => {
  const teams = getGithubTeams();

  res.send({
    teams: teams.map((team) => ({
      slug: team.slug,
      name: team.name,
      member_count: team.members.length
    }))
  });
});

export default endpoint.serve();
