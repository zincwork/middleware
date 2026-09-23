import {
  ALL_GITHUB_TEAMS,
  applyTeamFilter,
  normaliseAttribution,
  parseGithubTeamsConfig,
  resolveAuthors,
  resolveHeadBranches,
  resolveManagers,
  resolveShortcutTeamIds,
  resolveShortcutTeamIdsForSlugs,
  resolveTeamsForManager
} from '@/utils/githubTeams';

const CONFIG = JSON.stringify({
  org: 'zincwork',
  teams: [
    {
      slug: 'skipper',
      name: 'Skipper',
      members: ['ada', 'grace'],
      branch_prefixes: ['blue', 'skipper'],
      shortcut_team_id: 'sc-skipper',
      shortcut_team_name: 'Skipper',
      manager: 'Alisdair Little'
    },
    {
      slug: 'red-team',
      name: 'Red Team',
      members: ['linus'],
      branch_prefixes: ['red', 'red-team'],
      shortcut_team_id: 'sc-red',
      shortcut_team_name: 'Red Team',
      manager: 'Anton Khomchenko'
    },
    {
      slug: 'bliss',
      name: 'Bliss',
      members: ['bliss-dev'],
      branch_prefixes: ['gold'],
      shortcut_team_id: 'sc-bliss',
      manager: 'Lucila Sanjurjo'
    },
    {
      slug: 'integrations',
      name: 'Integration',
      members: ['int-dev'],
      branch_prefixes: [],
      shortcut_team_id: 'sc-integrations',
      // Deliberately a different capitalisation and some padding: the name is
      // typed by a person, not derived from an API.
      manager: '  lucila sanjurjo '
    },
    { slug: 'ghost', name: 'Ghost', members: [], branch_prefixes: [] }
  ]
});

const teams = parseGithubTeamsConfig(CONFIG);

describe('parseGithubTeamsConfig', () => {
  // toMatchObject, not toEqual. An exact-equality assertion here broke
  // silently when the Shortcut mapping was added to the parser, and would
  // break again on every future field. This asserts what the test is about
  // and lets the shape grow.
  it('parses members and branch prefixes', () => {
    expect(teams[0]).toMatchObject({
      slug: 'skipper',
      name: 'Skipper',
      members: ['ada', 'grace'],
      branch_prefixes: ['blue', 'skipper']
    });
  });

  it('parses the Shortcut mapping and the manager', () => {
    expect(teams[0]).toMatchObject({
      shortcut_team_id: 'sc-skipper',
      manager: 'Alisdair Little'
    });
  });

  it('defaults missing fields rather than throwing', () => {
    const parsed = parseGithubTeamsConfig(
      JSON.stringify({ teams: [{ slug: 'skipper' }] })
    );
    expect(parsed[0]).toMatchObject({
      slug: 'skipper',
      name: 'skipper',
      members: [],
      branch_prefixes: [],
      shortcut_team_id: null,
      shortcut_team_name: null,
      manager: null
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

describe('resolveShortcutTeamIds', () => {
  it('returns the mapped id for one team', () => {
    expect(resolveShortcutTeamIds(teams, 'skipper')).toEqual(['sc-skipper']);
  });

  it('returns nothing for an unmapped team, ALL, or null', () => {
    expect(resolveShortcutTeamIds(teams, 'ghost')).toEqual([]);
    expect(resolveShortcutTeamIds(teams, ALL_GITHUB_TEAMS)).toEqual([]);
    expect(resolveShortcutTeamIds(teams, null)).toEqual([]);
  });
});

describe('resolveTeamsForManager', () => {
  it('returns every team the manager holds', () => {
    // Lucila holds two. This is the case that makes the roll-up worth having
    // — a per-team view cannot answer "how is Lucila's area doing".
    expect(
      resolveTeamsForManager(teams, 'Lucila Sanjurjo').map((t) => t.slug)
    ).toEqual(['bliss', 'integrations']);
  });

  it('matches trimmed and case-insensitively', () => {
    // 'integrations' has the manager stored as '  lucila sanjurjo '.
    expect(
      resolveTeamsForManager(teams, '  LUCILA SANJURJO  ').map((t) => t.slug)
    ).toEqual(['bliss', 'integrations']);
  });

  it('returns nothing for an unknown, empty or null manager', () => {
    expect(resolveTeamsForManager(teams, 'Nobody')).toEqual([]);
    expect(resolveTeamsForManager(teams, '   ')).toEqual([]);
    expect(resolveTeamsForManager(teams, null)).toEqual([]);
  });
});

describe('resolveManagers', () => {
  it('groups teams under one manager despite differing spellings', () => {
    const managers = resolveManagers(teams);
    expect(managers.map((m) => m.manager)).toEqual([
      'Alisdair Little',
      'Anton Khomchenko',
      'Lucila Sanjurjo'
    ]);
    const lucila = managers.find((m) => m.manager === 'Lucila Sanjurjo');
    expect(lucila?.teams.map((t) => t.slug)).toEqual(['bliss', 'integrations']);
  });

  it('keeps the first spelling seen, so one stray capital is not a second manager', () => {
    const odd = parseGithubTeamsConfig(
      JSON.stringify({
        teams: [
          { slug: 'a', manager: 'Sam Smith' },
          { slug: 'b', manager: 'sam smith' }
        ]
      })
    );
    const managers = resolveManagers(odd);
    expect(managers).toHaveLength(1);
    expect(managers[0].manager).toBe('Sam Smith');
    expect(managers[0].teams.map((t) => t.slug)).toEqual(['a', 'b']);
  });

  it('omits teams with no manager set', () => {
    expect(
      resolveManagers(teams).flatMap((m) => m.teams.map((t) => t.slug))
    ).not.toContain('ghost');
  });
});

describe('resolveShortcutTeamIdsForSlugs', () => {
  it('collects ids across several teams', () => {
    expect(
      resolveShortcutTeamIdsForSlugs(teams, ['bliss', 'integrations'])
    ).toEqual(['sc-bliss', 'sc-integrations']);
  });

  it('skips unmapped and unknown slugs', () => {
    expect(
      resolveShortcutTeamIdsForSlugs(teams, ['skipper', 'ghost', 'nope'])
    ).toEqual(['sc-skipper']);
  });

  it('de-duplicates when two teams point at the same Shortcut team', () => {
    // Otherwise the id goes to the backend twice — harmless in the SQL, but
    // the team count shown beside the figure would be wrong.
    const shared = parseGithubTeamsConfig(
      JSON.stringify({
        teams: [
          { slug: 'a', shortcut_team_id: 'sc-1' },
          { slug: 'b', shortcut_team_id: 'sc-1' }
        ]
      })
    );
    expect(resolveShortcutTeamIdsForSlugs(shared, ['a', 'b'])).toEqual(['sc-1']);
  });

  it('returns nothing for an empty slug list', () => {
    expect(resolveShortcutTeamIdsForSlugs(teams, [])).toEqual([]);
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
