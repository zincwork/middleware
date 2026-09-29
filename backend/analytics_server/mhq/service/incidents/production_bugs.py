"""
Shortcut production bugs as incidents, attributed to the PR that caused them.

The chain, all from data Middleware already syncs:

    Shortcut bug labelled `production`           (Ticket, labels)
      -> its fix PR                              (TicketPullRequestMap, via sc-1234)
      -> the culprit PR the fix PR names         (INCIDENT_PRS_SETTING regex, e.g.
                                                  "fixes #123" or "hotfix/123-...")

The culprit PR is what change failure rate needs: the failed deployment is the
one that shipped it (see IncidentService.get_change_failure_rate_metrics_for_prs).

Every production bug is reported, attributed or not, with the reason when the
chain breaks, so gaps in the team convention are visible rather than silently
dropped.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from mhq.service.settings.models import IncidentPRFilter, IncidentPRsSetting
from mhq.store.models.code.filter import PRFilter
from mhq.store.models.code.pull_requests import PullRequest
from mhq.store.models.code.enums import PullRequestState
from mhq.store.models.incidents import Incident, IncidentStatus, IncidentType
from mhq.store.models.tickets import Ticket, TicketPullRequestMap
from mhq.utils.regex import check_regex

# Used when a team has not configured its own incident PR filters.
DEFAULT_CULPRIT_FILTERS: List[IncidentPRFilter] = [
    {"field": "title", "value": r"(?i)\b(?:fix(?:es|ed)?|revert(?:s|ed)?)\s+#(\d+)"},
    {"field": "head_branch", "value": r"(?i)^hotfix/(\d+)(?:\D|$)"},
]


class AttributionStatus:
    ATTRIBUTED = "attributed"
    NO_FIX_PR = "no_fix_pr"
    NO_CULPRIT_NAMED = "no_culprit_named"
    CULPRIT_NOT_FOUND = "culprit_not_found"
    CULPRIT_FILTERED_OUT = "culprit_filtered_out"


@dataclass
class ProductionBugAttribution:
    ticket: Ticket
    status: str
    fix_prs: List[PullRequest] = field(default_factory=list)
    fix_pr: Optional[PullRequest] = None
    culprit_pr: Optional[PullRequest] = None


def extract_culprit_number(
    pr: PullRequest, filters: List[IncidentPRFilter]
) -> Optional[str]:
    """The PR number a fix PR says it fixes, from its title or branch."""
    for pr_filter in filters:
        text = getattr(pr, pr_filter.get("field"), None)
        pattern = pr_filter.get("value")
        if not text or not pattern or not check_regex(pattern):
            continue
        match = re.search(pattern, text)
        if match and match.groups() and match.group(1):
            return match.group(1)
    return None


class ProductionBugAttributor:
    """Works out, for each production bug, which PR caused it.

    Repository access is injected so the logic can be tested without a DB:
      tickets_repo.get_bug_tickets_with_label(org_id, label, created_after)
      tickets_repo.get_pull_request_links_for_tickets(ticket_ids)
      code_repo.get_prs_by_ids(pr_ids, pr_filter=None)
      code_repo.get_repo_pr_by_number(repo_id, number)
    """

    def __init__(self, tickets_repo, code_repo):
        self._tickets_repo = tickets_repo
        self._code_repo = code_repo

    def attribute(
        self,
        org_id: str,
        team_repo_ids: List[str],
        setting: Optional[IncidentPRsSetting],
        created_after: datetime,
        pr_filter: PRFilter = None,
    ) -> List[ProductionBugAttribution]:
        label = (setting.production_bug_label if setting else None) or "production"
        filters = (setting.filters if setting else None) or DEFAULT_CULPRIT_FILTERS
        team_repo_ids = {str(repo_id) for repo_id in team_repo_ids}

        bugs: List[Ticket] = self._tickets_repo.get_bug_tickets_with_label(
            org_id, label, created_after
        )
        if not bugs:
            return []

        links: List[TicketPullRequestMap] = (
            self._tickets_repo.get_pull_request_links_for_tickets(
                [str(bug.id) for bug in bugs]
            )
        )
        linked_pr_ids = list(
            {str(link.pull_request_id) for link in links if link.pull_request_id}
        )
        prs_by_id: Dict[str, PullRequest] = (
            {str(pr.id): pr for pr in self._code_repo.get_prs_by_ids(linked_pr_ids)}
            if linked_pr_ids
            else {}
        )

        fix_prs_by_ticket: Dict[str, List[PullRequest]] = {}
        for link in links:
            pr = prs_by_id.get(str(link.pull_request_id))
            if pr and str(pr.repo_id) in team_repo_ids:
                fix_prs_by_ticket.setdefault(str(link.ticket_id), []).append(pr)

        results = [
            self._attribute_bug(bug, fix_prs_by_ticket.get(str(bug.id), []), filters)
            for bug in bugs
        ]
        return self._apply_pr_filter(results, pr_filter)

    def _attribute_bug(
        self,
        bug: Ticket,
        fix_prs: List[PullRequest],
        filters: List[IncidentPRFilter],
    ) -> ProductionBugAttribution:
        if not fix_prs:
            return ProductionBugAttribution(bug, AttributionStatus.NO_FIX_PR)

        # Merged fixes first, earliest first: the first real fix is the one
        # that restored service.
        fix_prs = sorted(
            fix_prs,
            key=lambda pr: (
                pr.state != PullRequestState.MERGED,
                pr.state_changed_at or datetime.max,
            ),
        )
        named_any = False
        for fix_pr in fix_prs:
            number = extract_culprit_number(fix_pr, filters)
            if not number:
                continue
            named_any = True
            culprit = self._code_repo.get_repo_pr_by_number(str(fix_pr.repo_id), number)
            if culprit and culprit.state == PullRequestState.MERGED:
                return ProductionBugAttribution(
                    bug,
                    AttributionStatus.ATTRIBUTED,
                    fix_prs=fix_prs,
                    fix_pr=fix_pr,
                    culprit_pr=culprit,
                )

        status = (
            AttributionStatus.CULPRIT_NOT_FOUND
            if named_any
            else AttributionStatus.NO_CULPRIT_NAMED
        )
        return ProductionBugAttribution(bug, status, fix_prs=fix_prs, fix_pr=fix_prs[0])

    def _apply_pr_filter(
        self, results: List[ProductionBugAttribution], pr_filter: PRFilter
    ) -> List[ProductionBugAttribution]:
        """With a squad filter, a bug is the squad's only if its culprit is."""
        if not pr_filter:
            return results
        culprit_ids = [
            str(r.culprit_pr.id)
            for r in results
            if r.status == AttributionStatus.ATTRIBUTED
        ]
        kept = (
            {
                str(pr.id)
                for pr in self._code_repo.get_prs_by_ids(culprit_ids, pr_filter)
            }
            if culprit_ids
            else set()
        )
        for result in results:
            if (
                result.status == AttributionStatus.ATTRIBUTED
                and str(result.culprit_pr.id) not in kept
            ):
                result.status = AttributionStatus.CULPRIT_FILTERED_OUT
        return results


def adapt_production_bug_incident(attribution: ProductionBugAttribution) -> Incident:
    """An attributed production bug as an Incident, keyed on its culprit PR.

    Keying on the culprit (as revert-PR incidents already do) means a bug and a
    revert of the same PR are one failure, not two.
    """
    bug, fix_pr, culprit = (
        attribution.ticket,
        attribution.fix_pr,
        attribution.culprit_pr,
    )
    fix_merged = fix_pr.state == PullRequestState.MERGED
    resolved_date = fix_pr.state_changed_at if fix_merged else bug.completed_at
    return Incident(
        id=culprit.id,
        provider=bug.provider,
        key=str(culprit.id),
        title=bug.title,
        incident_number=int(culprit.number),
        status=(
            IncidentStatus.RESOLVED.value
            if resolved_date
            else IncidentStatus.TRIGGERED.value
        ),
        # Detection: when the bug was raised. (Failure start is the failed
        # deployment, which CFR finds from the culprit PR.)
        creation_date=bug.provider_created_at,
        acknowledged_date=bug.provider_created_at,
        resolved_date=resolved_date,
        assigned_to=fix_pr.author,
        assignees=[fix_pr.author],
        url=bug.url,
        meta={
            "source": "shortcut_production_bug",
            "ticket_key": bug.key,
            "ticket_url": bug.url,
            "culprit_pr_id": str(culprit.id),
            "culprit_pr_number": culprit.number,
            "culprit_pr_url": culprit.url,
            "fix_pr_id": str(fix_pr.id),
            "fix_pr_number": fix_pr.number,
            "fix_pr_url": fix_pr.url,
        },
        incident_type=IncidentType.PRODUCTION_BUG,
    )


def get_incident_culprit_pr_id(incident: Incident) -> Optional[str]:
    """The PR an incident is blamed on, if known.

    Revert-PR incidents from the incidents sync carry the reverted PR in
    meta.original_pr; those built from incident PR filters are keyed on it.
    """
    meta = incident.meta or {}
    if incident.incident_type == IncidentType.PRODUCTION_BUG:
        return meta.get("culprit_pr_id")
    if incident.incident_type == IncidentType.REVERT_PR:
        original = meta.get("original_pr") or {}
        return str(original.get("id") or incident.key or "") or None
    return None
