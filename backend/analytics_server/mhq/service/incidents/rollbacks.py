"""
Rollbacks as incidents.

Redeploying an older revision undoes the deploys since it was last live. That
is a failed release even when nobody raised a bug or opened a revert PR, and
it needs no team convention: the deployment commits sync spots it (GitHub
compare status "behind") and records the deploys it undid.

The failure is tied to deployments, not PRs, because a rollback does not say
which PR in the release was at fault. So a rolled-back shared deploy fails for
every squad that had work in it.
"""

from datetime import datetime
from typing import Dict, List, Optional

from mhq.store.models.code.workflows import RepoWorkflow, RepoWorkflowRuns
from mhq.store.models.incidents import Incident, IncidentStatus, IncidentType


def adapt_rollback_incident(
    repo_workflow: RepoWorkflow,
    rollback_run: RepoWorkflowRuns,
    repo_name: Optional[str] = None,
) -> Optional[Incident]:
    shipped = (rollback_run.meta or {}).get("shipped") or {}
    rolled_back = shipped.get("rolled_back") or []
    if not rolled_back:
        return None

    failure_start = min(datetime.fromisoformat(r["conducted_at"]) for r in rolled_back)
    return Incident(
        id=rollback_run.id,
        provider=repo_workflow.provider.value,
        key=f"rollback|{rollback_run.id}",
        title=f"Rollback of {repo_name or repo_workflow.name} deploy",
        incident_number=0,
        status=IncidentStatus.RESOLVED.value,
        # From the first undone deploy going out to the rollback going out
        creation_date=failure_start,
        acknowledged_date=rollback_run.conducted_at,
        resolved_date=rollback_run.conducted_at,
        assigned_to=rollback_run.event_actor,
        assignees=[rollback_run.event_actor] if rollback_run.event_actor else [],
        url=rollback_run.html_url,
        meta={
            "source": "rollback",
            "failed_deployment_ids": [r["run_id"] for r in rolled_back],
            "rollback_deployment_id": str(rollback_run.id),
            "rolled_back_to_revision": (rollback_run.meta or {}).get("revision"),
            "rolled_back_from_revision": shipped.get("base"),
        },
        incident_type=IncidentType.ROLLBACK,
    )


def adapt_rollback_incidents(
    rollbacks: List[tuple], repo_names: Optional[Dict[str, str]] = None
) -> List[Incident]:
    repo_names = repo_names or {}
    incidents = [
        adapt_rollback_incident(
            repo_workflow, run, repo_names.get(str(repo_workflow.org_repo_id))
        )
        for repo_workflow, run in rollbacks
    ]
    return [incident for incident in incidents if incident]


def get_incident_failed_deployment_ids(incident: Incident) -> List[str]:
    """Workflow run ids an incident marks as failed directly (rollbacks)."""
    if incident.incident_type != IncidentType.ROLLBACK:
        return []
    return list((incident.meta or {}).get("failed_deployment_ids") or [])
