/**
 * GitHub team membership and branch conventions, used to scope DORA metrics.
 *
 * Two ways to decide which team a pull request belongs to:
 *
 *   author  — the PR author is in the team's GitHub membership list
 *   branch  — the PR's head branch starts with one of the team's prefixes
 *
 * Author attribution is complete but only reflects membership *today*, so
 * filtering a past quarter silently rewrites it when someone changes team.
 * Branch attribution is a permanent record of who owned the work at the time,
 * but only covers branches that follow the convention — about half of Zinc's,
 * measured at 293 of 568 remote branches across mvp-api and mvp-app.
 *
 * Neither is right on its own. Having both lets you compare them.
 *
 * Membership comes from `web-server/config/github_teams.json`. Regenerate it
 * with make_github_teams_config.py; edits take effect without a restart.
 *
 * Server-side only — it touches the filesystem. Client components must go via
 * `/api/resources/github_teams`.
 */

import fs from 'fs';
import path from 'path';

export type GithubTeam = {
  slug: string;
  name: string;
  members: string[];
  /** Branch name prefixes owned by this team, e.g. ['blue', 'skipper'] */
  branch_prefixes: string[];
};

export type GithubTeamsConfig = {
  org?: string;
  generated_at?: string;
  teams: GithubTeam[];
};

/** How to decide which team a pull request belongs to. */
export type Attribution = 'author' | 'branch';

export const ATTRIBUTION_AUTHOR: Attribution = 'author';
export const ATTRIBUTION_BRANCH: Attribution = 'branch';

/** Sentinel for "do not filter". Must match the frontend. */
export const ALL_GITHUB_TEAMS = 'ALL';

const CONFIG_PATH = path.join(process.cwd(), 'config', 'github_teams.json');

let cache: { mtimeMs: number; teams: GithubTeam[] } | null = null;

// --------------------------------------------------------------------------
// Pure helpers — exported for testing, no filesystem access
// --------------------------------------------------------------------------

export const parseGithubTeamsConfig = (raw: string): GithubTeam[] => {
  const parsed = JSON.parse(raw) as GithubTeamsConfig;
  if (!parsed || !Array.isArray(parsed.teams)) {
    throw new Error('github_teams.json must contain a "teams" array');
  }
  return parsed.teams
    .filter((team) => team && team.slug)
    .map((team) => ({
      slug: team.slug,
      name: team.name || team.slug,
      members: Array.isArray(team.members) ? team.members.filter(Boolean) : [],
      branch_prefixes: Array.isArray(team.branch_prefixes)
        ? team.branch_prefixes.filter(Boolean)
        : []
    }));
};

const findTeam = (teams: GithubTeam[], slug?: string | null) =>
  !slug || slug === ALL_GITHUB_TEAMS
    ? undefined
    : teams.find((team) => team.slug === slug);

export const resolveAuthors = (
  teams: GithubTeam[],
  slug?: string | null
): string[] => findTeam(teams, slug)?.members ?? [];

/**
 * Anchored POSIX regexes for a team's branch prefixes.
 *
 * The backend matches head_branch with the `~` operator, so these must be
 * regexes rather than plain strings. Anchored at the start and followed by a
 * slash so `red` cannot also match `red-team` or `redesign`.
 */
export const resolveHeadBranches = (
  teams: GithubTeam[],
  slug?: string | null
): string[] =>
  (findTeam(teams, slug)?.branch_prefixes ?? []).map(
    (prefix) => `^${escapeRegex(prefix)}/`
  );

const escapeRegex = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * Merge a team filter into a pr_filter payload.
 *
 * Every PR-derived metric funnels through PRFilter on the backend, so this
 * scopes lead time, deployment frequency (via PR attribution) and PR-derived
 * incidents together.
 *
 * `pr_filter` is legitimately null when no branch or repo filters apply, so
 * this has to be able to create the object rather than only extend it.
 */
export const applyTeamFilter = <T extends object>(
  prFilter: T | null | undefined,
  filter: { authors?: string[]; head_branches?: string[] }
): T | (T & typeof filter) | null => {
  const authors = filter.authors ?? [];
  const headBranches = filter.head_branches ?? [];

  if (!authors.length && !headBranches.length) return prFilter ?? null;

  return {
    ...((prFilter ?? {}) as T),
    ...(authors.length ? { authors } : {}),
    ...(headBranches.length ? { head_branches: headBranches } : {})
  };
};

export const normaliseAttribution = (value?: string | null): Attribution =>
  value === ATTRIBUTION_BRANCH ? ATTRIBUTION_BRANCH : ATTRIBUTION_AUTHOR;

// --------------------------------------------------------------------------
// Filesystem-backed accessors
// --------------------------------------------------------------------------

/**
 * Read the config, re-reading only when the file changes on disk so that
 * editing it does not require a restart.
 */
export const getGithubTeams = (): GithubTeam[] => {
  try {
    const { mtimeMs } = fs.statSync(CONFIG_PATH);
    if (cache && cache.mtimeMs === mtimeMs) return cache.teams;

    const teams = parseGithubTeamsConfig(fs.readFileSync(CONFIG_PATH, 'utf-8'));
    cache = { mtimeMs, teams };
    return teams;
  } catch (err) {
    // A missing or malformed config must not take the dashboard down — it just
    // means no team filtering is available.
    if ((err as NodeJS.ErrnoException)?.code !== 'ENOENT') {
      console.error('Could not read github_teams.json', err);
    }
    return [];
  }
};

export const getGithubTeamMembers = (slug?: string | null): string[] =>
  resolveAuthors(getGithubTeams(), slug);

/**
 * Apply the selected team's filter to a pr_filter, in the requested mode.
 *
 * Only one dimension is ever set, so the two attributions can be compared
 * against each other rather than compounding into an AND.
 */
export const withGithubTeamFilter = <T extends object>(
  prFilter: T | null | undefined,
  slug?: string | null,
  attribution?: string | null
) => {
  const teams = getGithubTeams();
  const mode = normaliseAttribution(attribution);

  return applyTeamFilter(
    prFilter,
    mode === ATTRIBUTION_BRANCH
      ? { head_branches: resolveHeadBranches(teams, slug) }
      : { authors: resolveAuthors(teams, slug) }
  );
};
