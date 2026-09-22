#!/usr/bin/env python3
"""
apply_circleci.py — install CircleCI deployment tracking into Middleware.

Builds on Option B. Copies in six new/replacement files and makes thirteen
anchored edits. Every edit matches a unique code snippet rather than a line
number, and re-running is a no-op rather than a double-apply.

Usage:
    python3 apply_circleci.py --repo ~/code/middleware --dry-run
    python3 apply_circleci.py --repo ~/code/middleware
    python3 apply_circleci.py --repo ~/code/middleware --revert

Requires Option B to be applied first — two edits anchor on it, and the
installer says so plainly rather than half-applying.

Backups are written alongside each modified file as <name>.option-c.bak.
Standard library only.
"""

import argparse
import os
import shutil
import sys

BACKUP_SUFFIX = ".option-c.bak"

NEW_FILES = [
    "backend/analytics_server/mhq/exapi/circle_ci.py",
    "backend/analytics_server/mhq/exapi/models/circle_ci.py",
    "backend/analytics_server/mhq/service/workflows/sync/etl_circle_ci_handler.py",
    "backend/analytics_server/tests/service/workflows/sync/test_etl_circle_ci_handler.py",
    "backend/analytics_server/tests/service/code/test_pr_filter_head_branches.py",
    "backend/analytics_server/tests/service/deployments/test_pr_attributed_frequency.py",
    "web-server/pages/api/resources/orgs/[org_id]/circleci_workflows.ts",
    # Replaces the Option B versions — adds branch-prefix attribution.
    "web-server/src/utils/githubTeams.ts",
    "web-server/src/utils/__tests__/githubTeams.test.ts",
]

# Edits that must find Option B's output. Checked first so a missing Option B
# produces one clear message instead of a wall of anchor failures.
#
# Each entry lists every acceptable marker: the Option B form, plus whatever
# this installer renames it to. Without the second form a re-run would report
# Option B as missing, because Option C replaces the symbol it looks for.
OPTION_B_MARKERS = [
    (
        "backend/analytics_server/mhq/store/models/code/filter.py",
        ['"authors": _authors_query(),'],
    ),
    (
        "web-server/pages/api/internal/team/[team_id]/dora_metrics.ts",
        ["withGithubTeamAuthors", "withGithubTeamFilter"],
    ),
]

# (path, description, old, new)
EDITS = [
    # ---------------------------------------------------------------- backend
    (
        "backend/analytics_server/mhq/service/workflows/sync/etl_workflows_factory.py",
        "import the CircleCI handler",
        """from mhq.service.workflows.sync.etl_github_actions_handler import (
    get_github_actions_etl_handler,
)""",
        """from mhq.service.workflows.sync.etl_circle_ci_handler import (
    get_circle_ci_etl_handler,
)
from mhq.service.workflows.sync.etl_github_actions_handler import (
    get_github_actions_etl_handler,
)""",
    ),
    (
        "backend/analytics_server/mhq/service/workflows/sync/etl_workflows_factory.py",
        "dispatch CIRCLE_CI to the new handler",
        """        if provider == RepoWorkflowProviders.GITHUB_ACTIONS.name:
            return get_github_actions_etl_handler(self.org_id)
        raise NotImplementedError(f"Unknown provider - {provider}")""",
        """        if provider == RepoWorkflowProviders.GITHUB_ACTIONS.name:
            return get_github_actions_etl_handler(self.org_id)
        if provider == RepoWorkflowProviders.CIRCLE_CI.name:
            return get_circle_ci_etl_handler(self.org_id)
        raise NotImplementedError(f"Unknown provider - {provider}")""",
    ),
    (
        "backend/analytics_server/mhq/service/workflows/integration.py",
        "let the sync look up a CircleCI integration",
        """WORKFLOW_INTEGRATION_BUCKET = [
    RepoWorkflowProviders.GITHUB_ACTIONS.value,
]""",
        """WORKFLOW_INTEGRATION_BUCKET = [
    RepoWorkflowProviders.GITHUB_ACTIONS.value,
    RepoWorkflowProviders.CIRCLE_CI.value,
]""",
    ),
    (
        "backend/analytics_server/mhq/store/models/integrations/enums.py",
        "allow a CircleCI token to be read back",
        """class UserIdentityProvider(Enum):
    GITHUB = "github"
    GITLAB = "gitlab\"""",
        """class UserIdentityProvider(Enum):
    GITHUB = "github"
    GITLAB = "gitlab"
    # CircleCI is a CI provider rather than a code host, but its token lives in
    # the same Integration table keyed on name, and get_access_token queries
    # Integration.name == provider.value.
    CIRCLECI = "circle_ci\"""",
    ),
    (
        "backend/analytics_server/mhq/store/models/code/filter.py",
        "add the head_branches field to PRFilter",
        """class PRFilter:
    authors: List[str] = None
    base_branches: List[str] = None""",
        """class PRFilter:
    authors: List[str] = None
    base_branches: List[str] = None
    head_branches: List[str] = None""",
    ),
    (
        "backend/analytics_server/mhq/store/models/code/filter.py",
        "wire head_branches into the SQL conditions map",
        """            return PullRequest.author.in_(self.authors)

        conditions = {
            "authors": _authors_query(),
            "base_branches": _base_branch_query(),""",
        """            return PullRequest.author.in_(self.authors)

        def _head_branches_query():
            if not self.head_branches:
                return None

            return or_(
                PullRequest.head_branch.op("~")(term) for term in self.head_branches
            )

        conditions = {
            "authors": _authors_query(),
            "base_branches": _base_branch_query(),
            "head_branches": _head_branches_query(),""",
    ),
    (
        "backend/analytics_server/mhq/service/code/pr_filter.py",
        "parse head_branches off the wire",
        """        authors: List[str] = self.__parse_pr_authors()
        base_branches: List[str] = self.__parse_pr_base_branches()
        repo_filters: Dict[str, Dict] = self.__parse_repo_filters()

        return PRFilter(
            authors=authors,
            base_branches=base_branches,
            repo_filters=repo_filters,
        )""",
        """        authors: List[str] = self.__parse_pr_authors()
        base_branches: List[str] = self.__parse_pr_base_branches()
        head_branches: List[str] = self.__parse_pr_head_branches()
        repo_filters: Dict[str, Dict] = self.__parse_repo_filters()

        return PRFilter(
            authors=authors,
            base_branches=base_branches,
            head_branches=head_branches,
            repo_filters=repo_filters,
        )""",
    ),
    (
        "backend/analytics_server/mhq/service/code/pr_filter.py",
        "add the head_branches parser",
        """    def __parse_pr_base_branches(self) -> List[str]:
        base_branches: List[str] = self.pr_filter.get("base_branches")
        if base_branches:
            base_branches: List[str] = regex_list(base_branches)
        return base_branches""",
        """    def __parse_pr_base_branches(self) -> List[str]:
        base_branches: List[str] = self.pr_filter.get("base_branches")
        if base_branches:
            base_branches: List[str] = regex_list(base_branches)
        return base_branches

    def __parse_pr_head_branches(self) -> List[str]:
        # Regex, like base_branches, because team branch prefixes are naturally
        # patterns ("^blue/"). Unlike authors, which is an exact IN list.
        head_branches: List[str] = self.pr_filter.get("head_branches")
        if head_branches:
            head_branches: List[str] = regex_list(head_branches)
        return head_branches""",
    ),
    (
        "backend/analytics_server/mhq/service/deployments/analytics.py",
        "import DeploymentStatus",
        """from mhq.service.deployments.models.models import (
    Deployment,
    DeploymentFrequencyMetrics,
)""",
        """from mhq.service.deployments.models.models import (
    Deployment,
    DeploymentFrequencyMetrics,
    DeploymentStatus,
)""",
    ),
    (
        "backend/analytics_server/mhq/service/deployments/analytics.py",
        "attribute the headline deployment frequency",
        """    ) -> DeploymentFrequencyMetrics:

        team_successful_deployments = (
            self.deployments_service.get_team_successful_deployments_in_interval(
                team_id, interval, pr_filter, workflow_filter
            )
        )

        return self._get_deployment_frequency_metrics(
            team_successful_deployments, interval
        )""",
        """    ) -> DeploymentFrequencyMetrics:

        team_successful_deployments = self._get_successful_deployments_for_metrics(
            team_id, interval, pr_filter, workflow_filter
        )

        return self._get_deployment_frequency_metrics(
            team_successful_deployments, interval
        )""",
    ),
    (
        "backend/analytics_server/mhq/service/deployments/analytics.py",
        "attribute the deployment frequency trend the same way",
        """    ) -> Dict[datetime, int]:

        team_successful_deployments = (
            self.deployments_service.get_team_successful_deployments_in_interval(
                team_id, interval, pr_filter, workflow_filter
            )
        )

        team_weekly_deployments = generate_expanded_buckets(""",
        """    ) -> Dict[datetime, int]:

        # Must use the same attribution as the headline figure — the card shows
        # both together and they have to agree.
        team_successful_deployments = self._get_successful_deployments_for_metrics(
            team_id, interval, pr_filter, workflow_filter
        )

        team_weekly_deployments = generate_expanded_buckets(""",
    ),
    (
        "backend/analytics_server/mhq/service/deployments/analytics.py",
        "add the PR-attribution helpers",
        """    def _map_prs_to_repo_id_and_base_branch(""",
        '''    def _get_successful_deployments_for_metrics(
        self,
        team_id: str,
        interval: Interval,
        pr_filter: PRFilter,
        workflow_filter: WorkflowFilter,
    ) -> List[Deployment]:
        """Successful deployments, attributed to the team when a filter is set.

        Workflow deployments (CircleCI, GitHub Actions) are filtered on branch
        only, so a PR-level filter such as an author or head-branch list would
        otherwise have no effect on this metric — leaving two filtered DORA
        cards beside two unfiltered ones, all looking equally authoritative.

        When such a filter is active, count a deployment only if it carried at
        least one PR that survived the filter: "how often did this team's work
        reach production".

        Two consequences, both deliberate:
          * a deploy carrying nothing attributable — a re-run, a config-only
            change — counts for nobody, so per-team figures sum to LESS than
            the org-wide figure;
          * a deploy carrying three teams' PRs counts once for each, so they
            can also sum to MORE. Which way it goes depends on the mix.
        """
        if not self._is_pr_attributed(pr_filter):
            return self.deployments_service.get_team_successful_deployments_in_interval(
                team_id, interval, pr_filter, workflow_filter
            )

        repo_id_to_deployments_with_prs = (
            self.get_team_all_deployments_in_interval_with_related_prs(
                team_id, interval, pr_filter, workflow_filter
            )
        )

        return [
            deployment
            for deployments_with_prs in repo_id_to_deployments_with_prs.values()
            for deployment, prs in deployments_with_prs.items()
            if prs and deployment.status == DeploymentStatus.SUCCESS
        ]

    @staticmethod
    def _is_pr_attributed(pr_filter: PRFilter) -> bool:
        """Is a team-level PR filter active?

        Deliberately ignores base_branches and repo_filters: production-branch
        filtering is always on, and must not push every request down the more
        expensive attributed path.
        """
        if not pr_filter:
            return False
        return bool(pr_filter.authors) or bool(pr_filter.head_branches)

    def _map_prs_to_repo_id_and_base_branch(''',
    ),
    # --------------------------------------------------------------- frontend
    (
        "web-server/pages/api/internal/team/[team_id]/dora_metrics.ts",
        "switch to the attribution-aware filter helper",
        """import { withGithubTeamAuthors } from '@/utils/githubTeams';""",
        """import { withGithubTeamFilter } from '@/utils/githubTeams';""",
    ),
    (
        "web-server/pages/api/internal/team/[team_id]/dora_metrics.ts",
        "accept the attribution query param",
        """  github_team: yup.string().optional().nullable()
});""",
        """  github_team: yup.string().optional().nullable(),
  attribution: yup.string().oneOf(['author', 'branch']).optional().nullable()
});""",
    ),
    (
        "web-server/pages/api/internal/team/[team_id]/dora_metrics.ts",
        "read attribution off the payload",
        """    branch_mode,
    github_team
  } = req.payload;""",
        """    branch_mode,
    github_team,
    attribution
  } = req.payload;""",
    ),
    (
        "web-server/pages/api/internal/team/[team_id]/dora_metrics.ts",
        "pass attribution into the filter",
        """        pr_filter: withGithubTeamAuthors(pr_filter, github_team as string)""",
        """        pr_filter: withGithubTeamFilter(
          pr_filter,
          github_team as string,
          attribution as string
        )""",
    ),
    (
        "web-server/src/components/GithubTeamSelector.tsx",
        "correct the notice for CircleCI deployments",
        """        Filtered to pull requests authored by <b>{selectedSlug}</b> members.
        Lead time and deployment frequency reflect this filter. Change failure
        rate and mean time to recovery only do so where incidents are derived
        from pull requests — treat them as org-wide otherwise.""",
        """        Filtered to <b>{selectedSlug}</b>. Lead time reflects the filter
        directly. Deployment frequency counts production deploys that carried
        at least one of the team&apos;s pull requests, so a deploy carrying
        nothing attributable counts for nobody and a deploy carrying several
        teams&apos; work counts for each — team figures will not sum to the
        org-wide number in either direction. Change failure rate and mean time
        to recovery only reflect the filter where incidents are derived from
        pull requests; treat them as org-wide otherwise.""",
    ),
]


# --------------------------------------------------------------------------

def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def added_lines(old, new):
    """The substantive lines `new` adds to `old`, used to detect a prior apply.

    Comparing the whole `new` block is too brittle: if a surrounding line has
    drifted, an edit that IS already applied gets reported as a failure.
    """
    old_lines = {line.strip() for line in old.splitlines()}
    return [
        line.strip()
        for line in new.splitlines()
        if line.strip() and line.strip() not in old_lines and len(line.strip()) > 12
    ]


def already_applied(text, old, new):
    if new in text:
        return True
    additions = added_lines(old, new)
    return bool(additions) and all(line in text for line in additions)


def anchor_hint(text, old):
    first = old.strip().splitlines()[0].strip()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if first and first in line:
            lo, hi = max(0, i - 2), min(len(lines), i + 9)
            numbered = "\n".join(
                "    {:>5} | {}".format(n + 1, lines[n]) for n in range(lo, hi)
            )
            return (
                "    Found the first anchor line at line {}, but the block below "
                "it differs.\n    Actual file content:\n{}".format(i + 1, numbered)
            )
    return "    Could not find '{}' anywhere in the file.".format(first)


def check_option_b(repo):
    missing = []
    for path, markers in OPTION_B_MARKERS:
        full = os.path.join(repo, path)
        if not os.path.isfile(full):
            missing.append((path, markers))
            continue
        text = read(full)
        if not any(marker in text for marker in markers):
            missing.append((path, markers))
    return missing


def apply_edits(repo, dry_run):
    applied = skipped = 0
    failures = []
    touched = set()

    for path, desc, old, new in EDITS:
        full = os.path.join(repo, path)
        if not os.path.isfile(full):
            failures.append((path, desc, "    File does not exist."))
            continue

        text = read(full)

        if already_applied(text, old, new):
            print("  already applied  {} — {}".format(path, desc))
            skipped += 1
            continue

        if old not in text:
            failures.append((path, desc, anchor_hint(text, old)))
            continue

        if text.count(old) > 1:
            failures.append((
                path, desc,
                "    The anchor appears {} times, so the edit is ambiguous. "
                "Apply this one by hand.".format(text.count(old)),
            ))
            continue

        print("  edit             {} — {}".format(path, desc))
        applied += 1

        if not dry_run:
            backup = full + BACKUP_SUFFIX
            if not os.path.exists(backup) and full not in touched:
                shutil.copy2(full, backup)
            touched.add(full)
            write(full, text.replace(old, new, 1))

    return applied, skipped, failures


def copy_new_files(repo, here, dry_run):
    copied = 0
    failures = []

    for rel in NEW_FILES:
        src = os.path.join(here, "files", rel)
        dest = os.path.join(repo, rel)

        if not os.path.isfile(src):
            failures.append((rel, "source file missing from ./files", ""))
            continue

        label = "replace" if os.path.exists(dest) else "new file"
        print("  {:<16} {}".format(label, rel))
        copied += 1

        if not dry_run:
            # Keep a backup of anything being replaced (the Option B files).
            if os.path.exists(dest) and not os.path.exists(dest + BACKUP_SUFFIX):
                shutil.copy2(dest, dest + BACKUP_SUFFIX)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)

    return copied, failures


def revert(repo, dry_run):
    restored = removed = 0

    for path, _, _, _ in EDITS:
        backup = os.path.join(repo, path) + BACKUP_SUFFIX
        if not os.path.exists(backup):
            continue
        print("  restore  {}".format(path))
        restored += 1
        if not dry_run:
            shutil.copy2(backup, os.path.join(repo, path))
            os.remove(backup)

    for rel in NEW_FILES:
        dest = os.path.join(repo, rel)
        backup = dest + BACKUP_SUFFIX
        if os.path.exists(backup):
            # A replaced Option B file — put the old version back.
            print("  restore  {}".format(rel))
            restored += 1
            if not dry_run:
                shutil.copy2(backup, dest)
                os.remove(backup)
        elif os.path.exists(dest):
            print("  remove   {}".format(rel))
            removed += 1
            if not dry_run:
                os.remove(dest)

    print("")
    print("Restored {} file(s), removed {} new file(s).".format(restored, removed))
    print("Option B is left in place. The CircleCI Integration row and the")
    print("RepoWorkflow rows are database state — undo those with")
    print("setup_circleci.py --revert.")
    if restored == 0 and removed == 0:
        print("")
        print("Nothing to revert. If you edited files by hand, use git:")
        print("    git -C {} checkout -- .".format(repo))


def main():
    ap = argparse.ArgumentParser(
        description="Install CircleCI deployment tracking into Middleware."
    )
    ap.add_argument("--repo", default=os.path.expanduser("~/code/middleware"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.expanduser(args.repo))
    here = os.path.dirname(os.path.abspath(__file__))

    if not os.path.isdir(os.path.join(repo, "web-server")):
        sys.exit(
            "{} does not look like a Middleware checkout — no web-server/ "
            "directory.\nPass the right path with --repo.".format(repo)
        )

    print("Repo: {}".format(repo))
    print("")

    if args.revert:
        revert(repo, args.dry_run)
        if args.dry_run:
            print("")
            print("Dry run — nothing changed.")
        return

    missing = check_option_b(repo)
    if missing:
        print("Option B does not appear to be applied. Two of these edits build")
        print("directly on it, so stopping rather than half-applying.")
        print("")
        for path, markers in missing:
            print("  {} — expected to contain one of: {}".format(
                path, ", ".join("'{}'".format(m) for m in markers)))
        print("")
        print("Run the Option B installer first:")
        print("    python3 ../option-b/apply_option_b.py --repo {}".format(repo))
        sys.exit(1)

    if args.dry_run:
        print("DRY RUN — nothing will be written.\n")

    copied, copy_failures = copy_new_files(repo, here, args.dry_run)
    applied, skipped, edit_failures = apply_edits(repo, args.dry_run)

    failures = copy_failures + edit_failures
    print("")
    print("{} file(s) copied, {} edit(s), {} already applied, {} problem(s)."
          .format(copied, applied, skipped, len(failures)))

    if failures:
        print("")
        print("PROBLEMS — these need doing by hand:")
        for path, desc, detail in failures:
            print("")
            print("  {}".format(path))
            print("    {}".format(desc))
            if detail:
                print(detail)
        print("")
        print("The README lists the exact before/after for every edit. Each edit")
        print("is independent, so nothing was left half-done.")
        sys.exit(1)

    if args.dry_run:
        print("")
        print("Dry run complete. Re-run without --dry-run to apply.")
        return

    print("")
    print("Next:")
    print("  1. Regenerate the team config so it carries branch prefixes:")
    print("       python3 make_github_teams_config.py \\")
    print("           --in zinc_teams.json \\")
    print("           --out {}/web-server/config/github_teams.json".format(repo))
    print("  2. Store the CircleCI token and configure the deploy jobs:")
    print("       export CIRCLECI_TOKEN=...")
    print("       python3 setup_circleci.py --dry-run")
    print("       python3 setup_circleci.py")
    print("  3. Restart with file watching, then trigger a sync:")
    print("       cd {} && docker compose watch".format(repo))
    print("  4. Once the sync has run, check the numbers:")
    print("       python3 verify_circleci.py")
    print("       python3 compare_attribution.py")
    print("")
    print("To undo the code: python3 apply_circleci.py --repo {} --revert".format(repo))


if __name__ == "__main__":
    main()
