from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class ShortcutTeam:
    id: str
    name: str
    mention_name: Optional[str]
    archived: bool
    raw: Dict = field(default_factory=dict)


@dataclass
class ShortcutWorkflowState:
    id: str
    name: str
    # backlog | unstarted | started | done
    state_type: str
    workflow_id: Optional[str] = None
    workflow_name: Optional[str] = None


@dataclass
class ShortcutStory:
    id: str
    name: Optional[str]
    story_type: Optional[str]
    app_url: Optional[str]
    team_id: Optional[str]
    epic_id: Optional[str]
    iteration_id: Optional[str]
    parent_story_id: Optional[str]
    workflow_id: Optional[str]
    workflow_state_id: Optional[str]
    owner_id: Optional[str]
    requested_by_id: Optional[str]
    estimate: Optional[int]
    labels: List[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    is_archived: bool
    raw: Dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """The human-readable reference, as used in branch names (sc-8765)."""
        return f"sc-{self.id}"


@dataclass
class ShortcutStateChange:
    from_state_id: Optional[str]
    to_state_id: Optional[str]
    actor: Optional[str]
    changed_at: datetime


@dataclass
class ShortcutGitLink:
    """A branch or pull request Shortcut recorded against a story.

    This is the bridge to Middleware's own PullRequest rows: Shortcut supplies
    the PR number, so the join needs no guessing.
    """

    repo_name: Optional[str]
    pr_number: Optional[int]
    branch_name: Optional[str]


@dataclass
class ShortcutHistoryEntry:
    changed_at: Optional[datetime]
    actor_name: Optional[str]
    actions: List[Dict]
    references: List[Dict]
    raw: Dict = field(default_factory=dict)
