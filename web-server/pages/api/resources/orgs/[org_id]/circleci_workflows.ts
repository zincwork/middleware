import * as yup from 'yup';

import { Endpoint } from '@/api-helpers/global';
import { CIProvider, Integration, WorkflowType } from '@/constants/integrations';
import { Table } from '@/constants/db';
import { db, dbRaw } from '@/utils/db';

/**
 * Configures CircleCI deploy jobs as a repo's deployment source.
 *
 * The existing teams/v2 route can only create GitHub Actions workflow rows —
 * it derives the CI provider from the *code* provider, so a GitHub-hosted repo
 * always gets GITHUB_ACTIONS. This route writes the CircleCI rows instead, and
 * flips the repo's deployment_type from PR_MERGE to WORKFLOW so Middleware
 * stops treating a merge into main as a deployment.
 *
 * `provider_workflow_id` holds "<workflow name>/<job name>", because in
 * CircleCI the deployment is a job inside a workflow, not the workflow itself.
 * The column is free text, and the compound value also keeps it clear of the
 * (org_repo_id, provider_workflow_id) unique index shared with GitHub Actions.
 */

const workflowSchema = yup.object().shape({
  repo_name: yup.string().required(),
  workflow_name: yup.string().required(),
  job_name: yup.string().required(),
  branch: yup.string().required(),
  project_slug: yup.string().required()
});

const pathnameSchema = yup.object().shape({
  org_id: yup.string().uuid().required()
});

const putSchema = yup.object().shape({
  workflows: yup.array().of(workflowSchema).required()
});

const deleteSchema = yup.object().shape({
  repo_names: yup.array().of(yup.string()).required()
});

const endpoint = new Endpoint(pathnameSchema);

const getActiveRepos = (org_id: ID) =>
  db(Table.OrgRepo)
    .select('id', 'name', 'org_name', 'default_branch')
    .where('org_id', org_id)
    .andWhere('is_active', true)
    .whereIn('provider', [Integration.GITHUB, Integration.GITLAB]);

endpoint.handle.GET(yup.object().shape({}), async (req, res) => {
  const rows = await db('RepoWorkflow')
    .leftJoin('OrgRepo', 'OrgRepo.id', 'RepoWorkflow.org_repo_id')
    .leftJoin({ tr: Table.TeamRepos }, 'tr.org_repo_id', 'OrgRepo.id')
    .select(
      'OrgRepo.name as repo_name',
      'RepoWorkflow.id',
      'RepoWorkflow.provider',
      'RepoWorkflow.provider_workflow_id',
      'RepoWorkflow.name',
      'RepoWorkflow.is_active',
      'RepoWorkflow.meta',
      'tr.deployment_type'
    )
    .where('OrgRepo.org_id', req.payload.org_id)
    .andWhere('RepoWorkflow.provider', CIProvider.CIRCLE_CI);

  res.send({ workflows: rows });
});

endpoint.handle.PUT(putSchema, async (req, res) => {
  const { org_id, workflows } = req.payload;

  const repos = await getActiveRepos(org_id);
  const reposByName = new Map(repos.map((r) => [r.name, r]));

  const missing = workflows
    .map((w) => w.repo_name)
    .filter((name) => !reposByName.has(name));

  if (missing.length) {
    return res.status(400).send({
      message:
        'These repos are not active in Middleware yet — assign them to a team ' +
        'and let a sync finish first: ' +
        missing.join(', '),
      known_repos: repos.map((r) => r.name)
    });
  }

  const rows = workflows.map((w) => ({
    org_repo_id: reposByName.get(w.repo_name).id,
    type: WorkflowType.DEPLOYMENT,
    provider: CIProvider.CIRCLE_CI,
    provider_workflow_id: `${w.workflow_name}/${w.job_name}`,
    name: `${w.workflow_name} / ${w.job_name}`,
    is_active: true,
    meta: JSON.stringify({
      project_slug: w.project_slug,
      branch: w.branch,
      workflow_name: w.workflow_name,
      job_name: w.job_name
    })
  }));

  const repoIds = rows.map((r) => r.org_repo_id);

  await dbRaw.transaction(async (trx) => {
    // Any other DEPLOYMENT workflow on these repos would double-count, so
    // stand them down rather than leaving two sources active.
    await trx('RepoWorkflow')
      .update({ is_active: false, updated_at: new Date() })
      .whereIn('org_repo_id', repoIds)
      .andWhere('type', WorkflowType.DEPLOYMENT);

    await trx('RepoWorkflow')
      .insert(rows)
      .onConflict(['org_repo_id', 'provider_workflow_id'])
      .merge();

    // Stop counting merges into main as deployments.
    await trx(Table.TeamRepos)
      .update({ deployment_type: 'WORKFLOW', updated_at: new Date() })
      .whereIn('org_repo_id', repoIds);
  });

  res.send({
    configured: rows.map((r) => ({
      org_repo_id: r.org_repo_id,
      provider_workflow_id: r.provider_workflow_id
    }))
  });
});

endpoint.handle.DELETE(deleteSchema, async (req, res) => {
  const { org_id, repo_names } = req.payload;

  const repos = await getActiveRepos(org_id);
  const repoIds = repos
    .filter((r) => repo_names.includes(r.name))
    .map((r) => r.id);

  if (!repoIds.length) return res.send({ reverted: [] });

  await dbRaw.transaction(async (trx) => {
    await trx('RepoWorkflow')
      .update({ is_active: false, updated_at: new Date() })
      .whereIn('org_repo_id', repoIds)
      .andWhere('provider', CIProvider.CIRCLE_CI);

    await trx(Table.TeamRepos)
      .update({ deployment_type: 'PR_MERGE', updated_at: new Date() })
      .whereIn('org_repo_id', repoIds);
  });

  res.send({ reverted: repo_names });
});

export default endpoint.serve();
