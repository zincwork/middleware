import * as yup from 'yup';

import { Endpoint } from '@/api-helpers/global';
import { Table } from '@/constants/db';
import {
  enc,
  INTEGRATION_CONFLICT_COLUMNS
} from '@/utils/auth-supplementary';
import { getGithubTeams } from '@/utils/githubTeams';
import { db } from '@/utils/db';

/**
 * Shortcut connection status and token storage.
 *
 * Deliberately narrow. The Shortcut-team-to-GitHub-team mapping is NOT here —
 * it lives in web-server/config/github_teams.json next to each team's member
 * logins, so the single "GitHub Team" dropdown drives both halves of the view:
 * member logins filter the PR and DORA metrics, the mapped Shortcut team id
 * filters the ticket metrics.
 *
 * This route exists rather than reusing /integration because that route
 * validates `provider` against the `Integration` enum, which has no Shortcut
 * member — and widening it would mean editing an existing file.
 *
 * Writes exactly one row: Integration, keyed on (org_id, 'shortcut').
 */

const PROVIDER = 'shortcut';

const pathnameSchema = yup.object().shape({
  org_id: yup.string().uuid().required()
});

const postSchema = yup.object().shape({
  token: yup.string().required()
});

const deleteSchema = yup.object().shape({
  confirm: yup.boolean().required()
});

const endpoint = new Endpoint(pathnameSchema);

endpoint.handle.GET(yup.object().shape({}), async (req, res) => {
  const { org_id } = req.payload;

  const [integration] = await db(Table.Integration)
    .select('name', 'created_at', 'updated_at')
    .where('org_id', org_id)
    .andWhere('name', PROVIDER);

  // The mapping is config, so report it from there rather than the database.
  const teams = getGithubTeams();
  const mapped = teams
    .filter((team) => team.shortcut_team_id)
    .map((team) => ({
      github_team: team.slug,
      github_team_name: team.name,
      member_count: team.members.length,
      shortcut_team_id: team.shortcut_team_id,
      shortcut_team_name: team.shortcut_team_name ?? null
    }));

  const [counts] = await db('Ticket')
    .where('org_id', org_id)
    .andWhere('provider', PROVIDER)
    .count('* as total');

  res.send({
    token_stored: Boolean(integration),
    token_updated_at: integration?.updated_at ?? null,
    mapped_teams: mapped,
    unmapped_github_teams: teams
      .filter((team) => !team.shortcut_team_id)
      .map((team) => team.slug),
    ticket_count: Number(counts?.total ?? 0)
  });
});

/** Stores the token. Upserts a single Integration row named 'shortcut'. */
endpoint.handle.POST(postSchema, async (req, res) => {
  const { org_id, token } = req.payload;

  await db(Table.Integration)
    .insert({
      org_id,
      name: PROVIDER,
      access_token_enc_chunks: enc(token),
      updated_at: new Date()
    })
    .onConflict(INTEGRATION_CONFLICT_COLUMNS)
    .merge();

  res.send({ status: 'OK' });
});

/**
 * Removes the stored token. The mapping is config — edit or regenerate
 * github_teams.json to change that. Synced tickets are left in place.
 */
endpoint.handle.DELETE(deleteSchema, async (req, res) => {
  const { org_id, confirm } = req.payload;
  if (!confirm) return res.status(400).send({ message: 'confirm must be true' });

  await db(Table.Integration)
    .where('org_id', org_id)
    .andWhere('name', PROVIDER)
    .del();

  res.send({
    status: 'OK',
    note:
      'Token removed; the ticket sync will now no-op. Tickets already synced ' +
      'are left in place. Team mapping is in github_teams.json.'
  });
});

export default endpoint.serve();
