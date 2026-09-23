/**
 * GitHub team membership, branch conventions, and the Shortcut team mapping.
 *
 * One dropdown — "Filter by the people in a GitHub team" — drives the whole
 * view. Behind each GitHub team sits everything needed to scope both halves:
 *
 *   members[]          -> author filter for the PR and DORA metrics
 *   branch_prefixes[]  -> alternative branch-based attribution
 *   shortcut_team_id   -> team filter for the ticket metrics
 *   manager            -> who the team rolls up to, for the manager view
 *
 * The Shortcut mapping lives here rather than in a database table on purpose.
 * Middleware's own Team is a group of REPOSITORIES, and Zinc's squads share
 * repositories — which is the entire reason this GitHub Team filter exists.
 * Mapping Shortcut onto the GitHub team keeps one team concept end to end, and
 * re-mapping is a config edit rather than a migration or a re-sync.
 *
 * Regenerate with make_github_teams_config.py; map Shortcut with
 * setup_shortcut.py. Edits take effect without a restart.
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
  /** Shortcut group id whose stories belong to this team, if mapped */
  shortcut_team_id?: string | null;
  shortcut_team_name?: string | null;
  /**
   * Who this team reports to. GitHub has no concept of a manager, so this is
   * set by set_managers.py and preserved across config regeneration. One
   * manager can hold several teams — Lucila holds both Bliss and Integration
   * — which is the whole reason the roll-up is keyed on the manager rather
   * than on a single team.
   */
  manager?: string | null;
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
        : [],
      shortcut_team_id: team.shortcut_team_id || null,
      shortcut_team_name: team.shortcut_team_name || null,
      manager: team.manager || null
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
 * Anchored at the start and followed by a slash so `red` cannot also match
 * `red-team` or `redesign`.
 */
export const resolveHeadBranches = (
  teams: GithubTeam[],
  slug?: string | null
): string[] =>
  (findTeam(teams, slug)?.branch_prefixes ?? []).map(
    (prefix) => `^${escapeRegex(prefix)}/`
  );

/**
 * The Shortcut team ids whose tickets belong to this GitHub team.
 *
 * An array rather than a single id so one GitHub team can later cover several
 * Shortcut teams without a config migration.
 */
export const resolveShortcutTeamIds = (
  teams: GithubTeam[],
  slug?: string | null
): string[] => {
  const id = findTeam(teams, slug)?.shortcut_team_id;
  return id ? [id] : [];
};

/**
 * The teams a manager holds, in config order.
 *
 * Matched case-insensitively and trimmed, because the manager name is typed
 * by a person rather than derived from an API.
 */
export const resolveTeamsForManager = (
  teams: GithubTeam[],
  manager?: string | null
): GithubTeam[] => {
  const wanted = (manager ?? '').trim().toLowerCase();
  if (!wanted) return [];
  return teams.filter(
    (team) => (team.manager ?? '').trim().toLowerCase() === wanted
  );
};

/** Every manager who holds at least one team, with their teams. */
export const resolveManagers = (
  teams: GithubTeam[]
): { manager: string; teams: GithubTeam[] }[] => {
  const byManager = new Map<string, { manager: string; teams: GithubTeam[] }>();

  for (const team of teams) {
    const manager = (team.manager ?? '').trim();
    if (!manager) continue;
    const key = manager.toLowerCase();
    // First spelling seen wins for display, so one stray capitalisation does
    // not produce two managers in the dropdown.
    const entry = byManager.get(key) ?? { manager, teams: [] };
    entry.teams.push(team);
    byManager.set(key, entry);
  }

  return [...byManager.values()].sort((a, b) =>
    a.manager.localeCompare(b.manager)
  );
};

/**
 * Shortcut team ids for a set of GitHub team slugs, de-duplicated.
 *
 * The de-duplication matters: two GitHub teams pointing at the same Shortcut
 * team would otherwise pass the same id twice, and the backend's IN clause
 * would be harmless but the team count printed next to it would be wrong.
 */
export const resolveShortcutTeamIdsForSlugs = (
  teams: GithubTeam[],
  slugs: string[]
): string[] => {
  const wanted = new Set(slugs);
  const ids = teams
    .filter((team) => wanted.has(team.slug))
    .map((team) => team.shortcut_team_id)
    .filter((id): id is string => Boolean(id));
  return [...new Set(ids)];
};

const escapeRegex = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * Merge a team filter into a pr_filter payload.
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

/** Shortcut team ids for the selected GitHub team, for the ticket metrics. */
export const getShortcutTeamIds = (slug?: string | null): string[] =>
  resolveShortcutTeamIds(getGithubTeams(), slug);

/** Every manager and the teams they hold, for the manager roll-up. */
export const getManagers = () => resolveManagers(getGithubTeams());

/** The teams one manager holds. */
export const getTeamsForManager = (manager?: string | null): GithubTeam[] =>
  resolveTeamsForManager(getGithubTeams(), manager);

/** Shortcut team ids across several GitHub teams, de-duplicated. */
export const getShortcutTeamIdsForSlugs = (slugs: string[]): string[] =>
  resolveShortcutTeamIdsForSlugs(getGithubTeams(), slugs);

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
