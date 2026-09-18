from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class CircleCIPipeline:
    """A CircleCI pipeline — one trigger of a project, on one commit."""

    id: str
    number: int
    branch: Optional[str]
    revision: Optional[str]
    actor: Optional[str]
    created_at: Optional[datetime]
    raw: Dict = field(default_factory=dict)


@dataclass
class CircleCIJob:
    """A single job inside a workflow run.

    This is the unit that matters for Zinc: `build-and-deploy` runs on every
    push, but only the `deploy-production` job inside it deploys anything.
    """

    id: str
    name: str
    status: str
    job_number: Optional[int]
    started_at: Optional[datetime]
    stopped_at: Optional[datetime]
    raw: Dict = field(default_factory=dict)


@dataclass
class CircleCIDeployJobRun:
    """A deploy job run, joined back to the pipeline that produced it.

    The pipeline carries the branch and the actor; the job carries the timing
    and the outcome. Both are needed to build a RepoWorkflowRuns row.
    """

    pipeline: CircleCIPipeline
    workflow_id: str
    workflow_name: str
    job: CircleCIJob
    project_slug: str

    @property
    def html_url(self) -> str:
        # The v2 jobs response has no URL of its own, so build the app link.
        if self.job.job_number is None:
            return (
                f"https://app.circleci.com/pipelines/{self.project_slug}"
                f"/{self.pipeline.number}/workflows/{self.workflow_id}"
            )
        return (
            f"https://app.circleci.com/pipelines/{self.project_slug}"
            f"/{self.pipeline.number}/workflows/{self.workflow_id}"
            f"/jobs/{self.job.job_number}"
        )
