import { useEffect, useState } from 'react';

import { handleApi } from '@/api-helpers/axios-api-instance';

type GithubTeamSummary = {
  slug: string;
  name: string;
  member_count: number;
  manager: string | null;
  has_shortcut_team: boolean;
};

/**
 * The distinct managers set in github_teams.json.
 *
 * Fetched rather than read directly: `getGithubTeams` touches the filesystem
 * and is server-side only. Local state rather than a redux slice because the
 * list changes only when someone edits a config file, so there is nothing to
 * keep in sync and nothing else needs it.
 *
 * A failure here leaves the roll-up panel saying no managers are set, which
 * is the same message as none being set. That is the right outcome: the rest
 * of the page is unaffected and there is nothing the reader can do about it.
 */
export const useManagers = () => {
  const [managers, setManagers] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;

    handleApi<{ teams: GithubTeamSummary[] }>('resources/github_teams')
      .then((response) => {
        if (cancelled) return;
        const names = response.teams
          .map((team) => (team.manager ?? '').trim())
          .filter(Boolean);
        // Case-insensitive de-duplication, keeping the first spelling seen,
        // matching how the server groups them. One stray capital must not
        // produce two entries in the dropdown.
        const seen = new Map<string, string>();
        for (const name of names) {
          const key = name.toLowerCase();
          if (!seen.has(key)) seen.set(key, name);
        }
        setManagers([...seen.values()].sort((a, b) => a.localeCompare(b)));
      })
      .catch(() => {
        if (!cancelled) setManagers([]);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return managers;
};
