from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.sql import func

from mhq.store import db


class Ticket(db.Model):
    """One row per provider story/issue.

    `provider_team_id` is the provider's own team id (a Shortcut group id).
    It is mapped onto a GITHUB team in web-server/config/github_teams.json,
    not onto Middleware's Team — Middleware's Team is a group of repositories,
    and Zinc's squads share repositories, which is the whole reason the GitHub
    Team filter exists. Keeping the mapping in config means re-mapping is
    never a migration or a re-sync.
    """

    __tablename__ = "Ticket"

    id = db.Column(UUID(as_uuid=True), primary_key=True)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("Organization.id"))
    provider = db.Column(db.String)
    idempotency_key = db.Column(db.String)
    key = db.Column(db.String)

    title = db.Column(db.String)
    ticket_type = db.Column(db.String)
    url = db.Column(db.String)

    provider_team_id = db.Column(db.String)
    provider_epic_id = db.Column(db.String)
    provider_iteration_id = db.Column(db.String)

    parent_idempotency_key = db.Column(db.String)
    is_subtask = db.Column(db.Boolean, default=False)

    owner = db.Column(db.String)
    requester = db.Column(db.String)
    estimate = db.Column(db.Integer)

    workflow_id = db.Column(db.String)
    current_state = db.Column(db.String)
    current_state_type = db.Column(db.String)

    priority = db.Column(db.String)
    labels = db.Column(ARRAY(db.String))

    provider_created_at = db.Column(db.DateTime(timezone=True))
    provider_updated_at = db.Column(db.DateTime(timezone=True))
    started_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))

    is_complete = db.Column(db.Boolean, default=False)
    # Cancelled is a `done`-type state in Shortcut. Tracked separately so it
    # can be excluded from throughput rather than silently counted as work.
    is_cancelled = db.Column(db.Boolean, default=False)

    meta = db.Column(JSONB, default="{}")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __hash__(self):
        return hash(self.id)


class TicketStateTransition(db.Model):
    """A single workflow state change, from the provider's history feed.

    This is what makes cycle time by state possible: the current state alone
    cannot tell you how long something sat in review.
    """

    __tablename__ = "TicketStateTransition"

    id = db.Column(UUID(as_uuid=True), primary_key=True)
    ticket_id = db.Column(UUID(as_uuid=True), db.ForeignKey("Ticket.id"))
    from_state = db.Column(db.String)
    from_state_type = db.Column(db.String)
    to_state = db.Column(db.String)
    to_state_type = db.Column(db.String)
    actor = db.Column(db.String)
    changed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __hash__(self):
        return hash(self.id)


class TicketPullRequestMap(db.Model):
    """Ticket to pull request, taken from the provider's own git events.

    Shortcut records branch pushes and PR events against the story, so this is
    a direct join on PR number rather than a guess. `pull_request_id` is
    resolved against Middleware's PullRequest rows where one exists, and left
    null otherwise so the mapping survives an unsynced or external repo.
    """

    __tablename__ = "TicketPullRequestMap"

    ticket_id = db.Column(UUID(as_uuid=True), db.ForeignKey("Ticket.id"), primary_key=True)
    repo_name = db.Column(db.String, primary_key=True)
    pr_number = db.Column(db.Integer, primary_key=True)
    branch_name = db.Column(db.String)
    pull_request_id = db.Column(UUID(as_uuid=True))
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TicketsBookmark(db.Model):
    """Incremental sync watermark, one row per (org, provider).

    Deliberately separate from the shared BookmarkService: giving the ticket
    layer its own watermark means no existing bookmark code changes at all.
    """

    __tablename__ = "TicketsBookmark"

    id = db.Column(UUID(as_uuid=True), primary_key=True)
    org_id = db.Column(UUID(as_uuid=True), db.ForeignKey("Organization.id"))
    provider = db.Column(db.String)
    bookmark = db.Column(db.String)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
