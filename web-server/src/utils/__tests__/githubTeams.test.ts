import {
  ALL_GITHUB_TEAMS,
  applyTeamFilter,
  normaliseAttribution,
  parseGithubTeamsConfig,
  resolveAuthors,
  resolveHeadBranches
} from '@/utils/githubTeams';

const CONFIG = JSON.stringify({
  org: 'zincwork',
  teams: [
    {
      slug: 'skipper',
      name: 'Skipper',
      members: ['ada', 'grace'],
      branch_prefixes: ['blue', 'skipper']
    },
    {
      slug: 'red-team',
      name: 'Red Team',
      members: ['linus'],
      branch_prefixes: ['red', 'red-team']
    },
    { slug: 'ghost', name: 'Ghost', members: [], branch_prefixes: [] }
  ]
});

const teams = parseGithubTeamsConfig(CONFIG);

describe('parseGithubTeamsConfig', () => {
  it('parses members and branch prefixes', () => {
    expect(teams[0]).toEqual({
      slug: 'skipper',
      name: 'Skipper',
      members: ['ada', 'grace'],
      branch_prefixes: ['blue', 'skipper']
    });
  });

  it('defaults missing fields rather than throwing', () => {
    const parsed = parseGithubTeamsConfig(
      JSON.stringify({ teams: [{ slug: 'skipper' }] })
    );
    expect(parsed[0]).toEqual({
      slug: 'skipper',
      name: 'skipper',
      members: [],
      branch_prefixes: []
    });
  });

  it('drops entries with no slug', () => {
    const parsed = parseGithubTeamsConfig(
      JSON.stringify({ teams: [{ name: 'No slug' }, { slug: 'ok' }] })
    );
    expect(parsed.map((t) => t.slug)).toEqual(['ok']);
  });

  it('throws when teams is missing', () => {
    expect(() => parseGithubTeamsConfig('{}')).toThrow(/teams/);
  });
});

describe('resolveAuthors', () => {
  it('returns the members of the named team', () => {
    expect(resolveAuthors(teams, 'skipper')).toEqual(['ada', 'grace']);
  });

  it('returns nothing for ALL, null, unknown or memberless teams', () => {
    expect(resolveAuthors(teams, ALL_GITHUB_TEAMS)).toEqual([]);
    expect(resolveAuthors(teams, null)).toEqual([]);
    expect(resolveAuthors(teams, 'nope')).toEqual([]);
    expect(resolveAuthors(teams, 'ghost')).toEqual([]);
  });
});

describe('resolveHeadBranches', () => {
  it('anchors each prefix and requires a trailing slash', () => {
    // Unanchored, `red` would also match `redesign/...`; without the slash it
    // would swallow `red-team/...` into Red Team's figures twice.
    expect(resolveHeadBranches(teams, 'skipper')).toEqual([
      '^blue/',
      '^skipper/'
    ]);
  });

  it('keeps sibling prefixes distinct', () => {
    const patterns = resolveHeadBranches(teams, 'red-team');
    expect(patterns).toEqual(['^red/', '^red-team/']);
    // A regex built for `red` must not match a `red-team` branch.
    expect(new RegExp('^red/').test('red-team/feat/x')).toBe(false);
    expect(new RegExp('^red/').test('red/feat/x')).toBe(true);
  });

  it('escapes regex metacharacters in prefixes', () => {
    const odd = parseGithubTeamsConfig(
      JSON.stringify({
        teams: [{ slug: 'x', branch_prefixes: ['a.b+c'] }]
      })
    );
    expect(resolveHeadBranches(odd, 'x')).toEqual(['^a\\.b\\+c/']);
  });

  it('returns nothing for ALL or a team with no prefixes', () => {
    expect(resolveHeadBranches(teams, ALL_GITHUB_TEAMS)).toEqual([]);
    expect(resolveHeadBranches(teams, 'ghost')).toEqual([]);
  });
});

describe('applyTeamFilter', () => {
  it('adds authors without disturbing the existing filter', () => {
    const prFilter = { base_branches: ['^main$'] };
    expect(applyTeamFilter(prFilter, { authors: ['ada'] })).toEqual({
      base_branches: ['^main$'],
      authors: ['ada']
    });
    expect(prFilter).toEqual({ base_branches: ['^main$'] });
  });

  it('adds head branches', () => {
    expect(
      applyTeamFilter({ base_branches: ['^main$'] }, { head_branches: ['^blue/'] })
    ).toEqual({ base_branches: ['^main$'], head_branches: ['^blue/'] });
  });

  it('creates a pr_filter when there was none', () => {
    expect(applyTeamFilter(null, { authors: ['ada'] })).toEqual({
      authors: ['ada']
    });
  });

  it('omits empty dimensions rather than sending empty arrays', () => {
    // An empty list must not reach the backend as `authors: []`, which would
    // be indistinguishable from "match nothing" if the guard ever moved.
    expect(
      applyTeamFilter({ base_branches: ['^main$'] }, { authors: [], head_branches: ['^blue/'] })
    ).toEqual({ base_branches: ['^main$'], head_branches: ['^blue/'] });
  });

  it('leaves pr_filter untouched when nothing is set', () => {
    const prFilter = { base_branches: ['^main$'] };
    expect(applyTeamFilter(prFilter, {})).toBe(prFilter);
    expect(applyTeamFilter(null, { authors: [], head_branches: [] })).toBeNull();
  });
});

describe('normaliseAttribution', () => {
  it('defaults to author', () => {
    expect(normaliseAttribution(null)).toBe('author');
    expect(normaliseAttribution(undefined)).toBe('author');
    expect(normaliseAttribution('nonsense')).toBe('author');
    expect(normaliseAttribution('author')).toBe('author');
  });

  it('recognises branch', () => {
    expect(normaliseAttribution('branch')).toBe('branch');
  });
});
