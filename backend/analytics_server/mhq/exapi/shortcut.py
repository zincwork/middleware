"""
Shortcut REST API v3 client.

Read-only. Only the endpoints needed to build ticket flow metrics:

    GET /api/v3/member                  -> token check
    GET /api/v3/groups                  -> teams
    GET /api/v3/workflows               -> workflow states and their types
    GET /api/v3/search/stories          -> stories, paginated by `next` token
    GET /api/v3/stories/{id}/history    -> state transitions and git events

Auth is a Shortcut API token in the `Shortcut-Token` header.

The rate limit is 200 requests per minute — far tighter than GitHub or
CircleCI — and a full backfill needs one history call per story. The throttle
below is therefore not an optimisation, it is load-bearing: without it a
backfill trips 429 within the first minute.

Docs: https://developer.shortcut.com/api/rest/v3
"""

import time
from collections import deque
from datetime import datetime
from http import HTTPStatus
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import requests

from mhq.exapi.models.shortcut import (
    ShortcutHistoryEntry,
    ShortcutStory,
    ShortcutTeam,
    ShortcutWorkflowState,
)
from mhq.utils.log import LOG

SHORTCUT_BASE_URL = "https://api.app.shortcut.com/api/v3"

# Documented limit is 200/minute. Leave headroom for anything else using the
# same token (the MCP connector, a colleague's script).
RATE_LIMIT_PER_MINUTE = 170
RATE_WINDOW_SECONDS = 60

MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 10

# Hard ceiling on pagination so a misconfigured query cannot walk forever.
MAX_SEARCH_PAGES = 200
SEARCH_PAGE_SIZE = 25


def parse_shortcut_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        LOG.warning(f"[Shortcut] Could not parse timestamp: {value}")
        return None


class ShortcutApiService:
    def __init__(self, access_token: str, base_url: str = None):
        self._token = access_token
        self.base_url = (base_url or SHORTCUT_BASE_URL).rstrip("/")
        self.headers = {
            "Shortcut-Token": self._token or "",
            "Content-Type": "application/json",
        }
        self._request_times = deque()

    # ------------------------------------------------------------------
    # HTTP with throttling
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        now = time.monotonic()
        while self._request_times and now - self._request_times[0] > RATE_WINDOW_SECONDS:
            self._request_times.popleft()

        if len(self._request_times) >= RATE_LIMIT_PER_MINUTE:
            sleep_for = RATE_WINDOW_SECONDS - (now - self._request_times[0]) + 0.5
            if sleep_for > 0:
                LOG.info(f"[Shortcut] Throttling for {sleep_for:.1f}s")
                time.sleep(sleep_for)

        self._request_times.append(time.monotonic())

    def _get(self, path: str, params: Dict = None) -> Optional[Dict]:
        url = path if path.startswith("http") else f"{self.base_url}{path}"

        for attempt in range(MAX_RETRIES):
            self._throttle()
            response = requests.get(
                url, headers=self.headers, params=params or {}, timeout=30
            )

            if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                wait = RETRY_BACKOFF_SECONDS * (attempt + 1)
                LOG.warning(f"[Shortcut] 429 on {path}, backing off {wait}s")
                time.sleep(wait)
                continue

            if response.status_code == HTTPStatus.NOT_FOUND:
                # A deleted or invisible story. Empty rather than fatal so one
                # bad id cannot stop the sync.
                LOG.warning(f"[Shortcut] 404 for {path}")
                return None

            if response.status_code in (
                HTTPStatus.UNAUTHORIZED,
                HTTPStatus.FORBIDDEN,
            ):
                raise Exception(
                    f"Shortcut rejected the token ({response.status_code}) for {path}. "
                    "Check it is current and has not been revoked."
                )

            if response.status_code != HTTPStatus.OK:
                raise Exception(
                    f"Shortcut returned {response.status_code} for {path}: "
                    f"{response.text[:300]}"
                )

            return response.json()

        raise Exception(f"Shortcut rate limited {path} after {MAX_RETRIES} attempts")

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def check_token(self) -> bool:
        try:
            return bool(self._get("/member"))
        except Exception as e:
            LOG.error(f"[Shortcut] Token check failed: {str(e)}")
            return False

    def get_teams(self) -> List[ShortcutTeam]:
        payload = self._get("/groups") or []
        return [
            ShortcutTeam(
                id=raw.get("id"),
                name=raw.get("name"),
                mention_name=raw.get("mention_name"),
                archived=bool(raw.get("archived")),
                raw=raw,
            )
            for raw in payload
        ]

    def get_workflow_states(self) -> Dict[str, ShortcutWorkflowState]:
        """All workflow states across all workflows, keyed by state id.

        Flattened because a story references a state id directly and we never
        need to know which workflow it came from.
        """
        payload = self._get("/workflows") or []
        states: Dict[str, ShortcutWorkflowState] = {}
        for workflow in payload:
            for state in workflow.get("states") or []:
                states[str(state.get("id"))] = ShortcutWorkflowState(
                    id=str(state.get("id")),
                    name=state.get("name"),
                    state_type=state.get("type"),
                    workflow_id=str(workflow.get("id")),
                    workflow_name=workflow.get("name"),
                )
        return states

    def search_stories(
        self, query: str, max_pages: int = MAX_SEARCH_PAGES
    ) -> List[ShortcutStory]:
        """Walk the `next`-token pagination of the story search endpoint."""
        stories: List[ShortcutStory] = []
        params = {"query": query, "page_size": SEARCH_PAGE_SIZE}
        next_token = None

        for _ in range(max_pages):
            if next_token:
                params = {
                    "query": query,
                    "page_size": SEARCH_PAGE_SIZE,
                    "next": next_token,
                }

            payload = self._get("/search/stories", params)
            if not payload:
                break

            stories.extend(self._adapt_story(raw) for raw in payload.get("data") or [])

            next_url = payload.get("next")
            if not next_url:
                break
            next_token = self._extract_next_token(next_url)
            if not next_token:
                break
        else:
            LOG.warning(
                f"[Shortcut] Hit the {max_pages}-page cap for query '{query}'. "
                "Some stories may be missing from this sync."
            )

        return stories

    def get_story_history(self, story_id: str) -> List[ShortcutHistoryEntry]:
        payload = self._get(f"/stories/{story_id}/history")
        if not payload:
            return []
        return [
            ShortcutHistoryEntry(
                changed_at=parse_shortcut_datetime(raw.get("changed_at")),
                actor_name=raw.get("actor_name") or raw.get("member_id"),
                actions=raw.get("actions") or [],
                references=raw.get("references") or [],
                raw=raw,
            )
            for raw in payload
        ]

    # ------------------------------------------------------------------
    # Adapters
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_next_token(next_url: str) -> Optional[str]:
        """Pull the `next` parameter out of the URL Shortcut hands back."""
        try:
            parsed = parse_qs(urlparse(next_url).query)
            values = parsed.get("next") or []
            return values[0] if values else None
        except Exception:
            LOG.warning(f"[Shortcut] Could not parse next URL: {next_url}")
            return None

    @staticmethod
    def _adapt_story(raw: Dict) -> ShortcutStory:
        owner_ids = raw.get("owner_ids") or []
        return ShortcutStory(
            id=str(raw.get("id")),
            name=raw.get("name"),
            story_type=raw.get("story_type"),
            app_url=raw.get("app_url"),
            team_id=raw.get("group_id"),
            epic_id=str(raw["epic_id"]) if raw.get("epic_id") else None,
            iteration_id=(
                str(raw["iteration_id"]) if raw.get("iteration_id") else None
            ),
            parent_story_id=(
                str(raw["parent_story_id"]) if raw.get("parent_story_id") else None
            ),
            workflow_id=str(raw.get("workflow_id")) if raw.get("workflow_id") else None,
            workflow_state_id=(
                str(raw["workflow_state_id"]) if raw.get("workflow_state_id") else None
            ),
            owner_id=owner_ids[0] if owner_ids else None,
            requested_by_id=raw.get("requested_by_id"),
            estimate=raw.get("estimate"),
            labels=[
                label.get("name")
                for label in (raw.get("labels") or [])
                if label.get("name")
            ],
            created_at=parse_shortcut_datetime(raw.get("created_at")),
            updated_at=parse_shortcut_datetime(raw.get("updated_at")),
            started_at=parse_shortcut_datetime(raw.get("started_at")),
            completed_at=parse_shortcut_datetime(raw.get("completed_at")),
            is_archived=bool(raw.get("archived")),
            raw=raw,
        )
