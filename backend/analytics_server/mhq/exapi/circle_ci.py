"""
CircleCI API v2 client.

Only the read endpoints needed to find deployments:

    GET /v2/me                                  -> token check
    GET /v2/project/{project_slug}/pipeline      -> pipelines (branch, revision, actor)
    GET /v2/pipeline/{pipeline_id}/workflow      -> workflow runs of a pipeline
    GET /v2/workflow/{workflow_id}/job           -> jobs of a workflow run

Three levels deep because that is where the signal is. Zinc's `build-and-deploy`
workflow runs on every push to every branch; the deployment is the
`deploy-production` job inside it, behind a manual approval gate.

Auth is a CircleCI personal API token in the `Circle-Token` header.
Docs: https://circleci.com/docs/api/v2/
"""

import time
from datetime import datetime
from http import HTTPStatus
from typing import Callable, Dict, List, Optional

import requests

from mhq.exapi.models.circle_ci import CircleCIJob, CircleCIPipeline
from mhq.utils.log import LOG

CIRCLE_CI_BASE_URL = "https://circleci.com/api/v2"
PAGE_SIZE_HINT = 100

# The v2 API allows roughly 1,000 requests a minute per token, so hitting 429
# means something is wrong rather than routine. Back off, but do not grind.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

# Stop conditions for the pipeline walk. /project/{slug}/pipeline returns newest
# first with no date filter, so without a bound the first sync would page
# through the project's entire history.
MAX_PIPELINE_PAGES = 50


def parse_circle_ci_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a CircleCI timestamp.

    CircleCI returns fractional seconds ("2026-09-04T10:15:32.123Z") where
    GitHub does not, so Middleware's ISO_8601_DATE_FORMAT cannot be reused.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        LOG.warning(f"[CircleCI] Could not parse timestamp: {value}")
        return None


class CircleCIApiService:
    def __init__(self, access_token: str, base_url: str = None):
        self._token = access_token
        self.base_url = (base_url or CIRCLE_CI_BASE_URL).rstrip("/")
        self.headers = {
            "Circle-Token": self._token or "",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Dict = None) -> Dict:
        url = f"{self.base_url}{path}"

        for attempt in range(MAX_RETRIES):
            response = requests.get(
                url, headers=self.headers, params=params or {}, timeout=30
            )

            if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                wait = RETRY_BACKOFF_SECONDS * (attempt + 1)
                LOG.warning(f"[CircleCI] 429 on {path}, retrying in {wait}s")
                time.sleep(wait)
                continue

            if response.status_code == HTTPStatus.NOT_FOUND:
                # A project or pipeline we cannot see. Treated as empty rather
                # than fatal, so one bad repo does not stop the whole sync.
                LOG.warning(f"[CircleCI] 404 for {path}")
                return {}

            if response.status_code in (
                HTTPStatus.UNAUTHORIZED,
                HTTPStatus.FORBIDDEN,
            ):
                raise Exception(
                    f"CircleCI rejected the token ({response.status_code}) for {path}. "
                    "Check the token is current and has access to the project."
                )

            if response.status_code != HTTPStatus.OK:
                raise Exception(
                    f"CircleCI returned {response.status_code} for {path}: "
                    f"{response.text[:300]}"
                )

            return response.json()

        raise Exception(f"CircleCI rate limited {path} after {MAX_RETRIES} attempts")

    def _get_paginated(
        self,
        path: str,
        params: Dict = None,
        max_pages: int = MAX_PIPELINE_PAGES,
        should_stop: Callable[[List[Dict]], bool] = None,
    ) -> List[Dict]:
        """Walk a token-paginated collection.

        `should_stop` is checked after each page so callers can bail out once
        the results are older than their bookmark — without it, the first sync
        of an old project walks everything.
        """
        items: List[Dict] = []
        page_token = None

        for _ in range(max_pages):
            merged = dict(params or {})
            if page_token:
                merged["page-token"] = page_token

            payload = self._get(path, merged)
            page = payload.get("items") or []
            items.extend(page)

            if should_stop and should_stop(page):
                break

            page_token = payload.get("next_page_token")
            if not page_token:
                break

        return items

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def check_token(self) -> bool:
        try:
            return bool(self._get("/me"))
        except Exception as e:
            LOG.error(f"[CircleCI] Token check failed: {str(e)}")
            return False

    def get_pipelines(
        self, project_slug: str, branch: str, since: datetime
    ) -> List[CircleCIPipeline]:
        """Pipelines for one branch, newest first, stopping at `since`.

        Filtering by branch is what keeps this affordable: production deploys
        only happen on the production branch, so there is no reason to walk
        every feature branch's CI runs.
        """
        reached_bookmark = {"value": False}

        def _should_stop(page: List[Dict]) -> bool:
            for raw in page:
                created = parse_circle_ci_datetime(raw.get("created_at"))
                if created and since and created < since:
                    reached_bookmark["value"] = True
                    return True
            return False

        raw_pipelines = self._get_paginated(
            f"/project/{project_slug}/pipeline",
            params={"branch": branch},
            should_stop=_should_stop,
        )

        if not reached_bookmark["value"] and raw_pipelines:
            LOG.warning(
                f"[CircleCI] Hit the {MAX_PIPELINE_PAGES}-page cap for "
                f"{project_slug} without reaching the bookmark. Older "
                "deployments may be missing from this sync."
            )

        pipelines = [self._adapt_pipeline(raw) for raw in raw_pipelines]
        return [
            p
            for p in pipelines
            if p.created_at is None or since is None or p.created_at >= since
        ]

    def get_pipeline_workflows(self, pipeline_id: str) -> List[Dict]:
        payload = self._get(f"/pipeline/{pipeline_id}/workflow")
        return payload.get("items") or []

    def get_workflow_jobs(self, workflow_id: str) -> List[CircleCIJob]:
        payload = self._get(f"/workflow/{workflow_id}/job")
        return [self._adapt_job(raw) for raw in (payload.get("items") or [])]

    # ------------------------------------------------------------------
    # Adapters
    # ------------------------------------------------------------------

    @staticmethod
    def _adapt_pipeline(raw: Dict) -> CircleCIPipeline:
        vcs = raw.get("vcs") or {}
        trigger = raw.get("trigger") or {}
        actor = trigger.get("actor") or {}
        return CircleCIPipeline(
            id=raw.get("id"),
            number=raw.get("number"),
            branch=vcs.get("branch"),
            revision=vcs.get("revision"),
            actor=actor.get("login"),
            created_at=parse_circle_ci_datetime(raw.get("created_at")),
            raw=raw,
        )

    @staticmethod
    def _adapt_job(raw: Dict) -> CircleCIJob:
        return CircleCIJob(
            id=raw.get("id"),
            name=raw.get("name"),
            status=raw.get("status"),
            job_number=raw.get("job_number"),
            started_at=parse_circle_ci_datetime(raw.get("started_at")),
            stopped_at=parse_circle_ci_datetime(raw.get("stopped_at")),
            raw=raw,
        )
