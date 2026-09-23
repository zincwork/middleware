-- Ticket layer for the Cockpit view. Sourced from Shortcut.
--
-- Purely additive: four new tables, no ALTER of anything existing, no data
-- movement. `dbmate up` applies only pending migrations and init_db.sh does
-- not drop the database, so running this leaves every existing row untouched.
--
-- The down section reverses it completely. (Careful: a comment line beginning
-- "-- migrate:down" would be parsed by dbmate as the section marker itself,
-- which is why this sentence is phrased the way it is.)

-- migrate:up

CREATE TABLE IF NOT EXISTS public."Ticket" (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    provider character varying NOT NULL,

    -- Stable provider identity. For Shortcut this is the story's numeric id
    -- as a string, which is what every other entity references.
    idempotency_key character varying NOT NULL,
    key character varying,

    title character varying,
    ticket_type character varying,
    url character varying,

    -- The provider's own team/group id. Mapped onto a GITHUB team in
    -- web-server/config/github_teams.json, not onto Middleware's Team — see
    -- the note further down.
    provider_team_id character varying,
    provider_epic_id character varying,
    provider_iteration_id character varying,

    -- Parent story, if this is a sub-task. Counting rules treat the parent as
    -- the unit of work, so this is what lets sub-tasks be excluded.
    parent_idempotency_key character varying,
    is_subtask boolean DEFAULT false NOT NULL,

    owner character varying,
    requester character varying,
    estimate integer,

    workflow_id character varying,
    current_state character varying,
    -- backlog | unstarted | started | done. The provider's own bucketing.
    current_state_type character varying,

    priority character varying,
    labels character varying[],

    -- Provider timestamps, distinct from this row's own created_at/updated_at.
    provider_created_at timestamp with time zone,
    provider_updated_at timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,

    is_complete boolean DEFAULT false NOT NULL,
    -- Cancelled is a `done`-type state in Shortcut. Counting it as completed
    -- would inflate throughput and flatter cycle time, so it is tracked
    -- separately rather than inferred.
    is_cancelled boolean DEFAULT false NOT NULL,

    meta jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT "Ticket_pkey" PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ticket_org_provider_idempotency_key
    ON public."Ticket" (org_id, provider, idempotency_key);
CREATE INDEX IF NOT EXISTS ticket_org_id_completed_at
    ON public."Ticket" (org_id, completed_at);
CREATE INDEX IF NOT EXISTS ticket_provider_team_id
    ON public."Ticket" (provider_team_id);
CREATE INDEX IF NOT EXISTS ticket_provider_epic_id
    ON public."Ticket" (provider_epic_id);
CREATE INDEX IF NOT EXISTS ticket_parent_idempotency_key
    ON public."Ticket" (parent_idempotency_key);


CREATE TABLE IF NOT EXISTS public."TicketStateTransition" (
    id uuid NOT NULL,
    ticket_id uuid NOT NULL,
    from_state character varying,
    from_state_type character varying,
    to_state character varying,
    to_state_type character varying,
    actor character varying,
    changed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT "TicketStateTransition_pkey" PRIMARY KEY (id)
);

-- One transition per ticket per instant per destination. Makes the sync
-- idempotent without needing to diff history.
CREATE UNIQUE INDEX IF NOT EXISTS ticket_state_transition_unique
    ON public."TicketStateTransition" (ticket_id, changed_at, to_state);
CREATE INDEX IF NOT EXISTS ticket_state_transition_ticket_id
    ON public."TicketStateTransition" (ticket_id, changed_at);


-- Shortcut records branch pushes and pull request events in story history, so
-- the ticket -> PR link is a direct join on PR number rather than a heuristic.
CREATE TABLE IF NOT EXISTS public."TicketPullRequestMap" (
    ticket_id uuid NOT NULL,
    repo_name character varying NOT NULL,
    pr_number integer NOT NULL,
    branch_name character varying,
    -- Resolved against Middleware's own PullRequest rows where possible.
    -- Nullable because the PR may not be synced yet, or at all.
    pull_request_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT "TicketPullRequestMap_pkey" PRIMARY KEY (ticket_id, repo_name, pr_number)
);

CREATE INDEX IF NOT EXISTS ticket_pull_request_map_pull_request_id
    ON public."TicketPullRequestMap" (pull_request_id);


-- NOTE ON TEAM MAPPING
--
-- There is deliberately no table mapping provider teams onto Middleware teams.
-- Middleware's own Team is a group of REPOSITORIES, which is the wrong axis:
-- Zinc's squads share repositories, which is exactly why the GitHub Team
-- filter exists.
--
-- Instead, the Shortcut team is mapped onto the GITHUB team in
-- web-server/config/github_teams.json, alongside that team's member logins.
-- One dropdown then drives both halves: member logins filter the PR and DORA
-- metrics, and the mapped Shortcut team id filters the ticket metrics.
-- Re-mapping is a config edit, never a migration or a re-sync.


-- Incremental sync watermark, one row per (org, provider). Deliberately its
-- own table rather than an entry in the shared BookmarkService, so the ticket
-- layer needs no change to existing bookmark code.
CREATE TABLE IF NOT EXISTS public."TicketsBookmark" (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    provider character varying NOT NULL,
    bookmark character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT "TicketsBookmark_pkey" PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS tickets_bookmark_org_provider
    ON public."TicketsBookmark" (org_id, provider);


-- Foreign keys added last so table creation order does not matter.
-- ON DELETE CASCADE only ever removes ticket-layer rows.
ALTER TABLE public."Ticket"
    ADD CONSTRAINT "Ticket_org_id_fkey"
    FOREIGN KEY (org_id) REFERENCES public."Organization"(id);

ALTER TABLE public."TicketStateTransition"
    ADD CONSTRAINT "TicketStateTransition_ticket_id_fkey"
    FOREIGN KEY (ticket_id) REFERENCES public."Ticket"(id) ON DELETE CASCADE;

ALTER TABLE public."TicketPullRequestMap"
    ADD CONSTRAINT "TicketPullRequestMap_ticket_id_fkey"
    FOREIGN KEY (ticket_id) REFERENCES public."Ticket"(id) ON DELETE CASCADE;

ALTER TABLE public."TicketsBookmark"
    ADD CONSTRAINT "TicketsBookmark_org_id_fkey"
    FOREIGN KEY (org_id) REFERENCES public."Organization"(id);


-- migrate:down

ALTER TABLE IF EXISTS public."TicketsBookmark" DROP CONSTRAINT IF EXISTS "TicketsBookmark_org_id_fkey";
ALTER TABLE IF EXISTS public."TicketPullRequestMap" DROP CONSTRAINT IF EXISTS "TicketPullRequestMap_ticket_id_fkey";
ALTER TABLE IF EXISTS public."TicketStateTransition" DROP CONSTRAINT IF EXISTS "TicketStateTransition_ticket_id_fkey";
ALTER TABLE IF EXISTS public."Ticket" DROP CONSTRAINT IF EXISTS "Ticket_org_id_fkey";

DROP TABLE IF EXISTS public."TicketsBookmark";
DROP TABLE IF EXISTS public."TicketPullRequestMap";
DROP TABLE IF EXISTS public."TicketStateTransition";
DROP TABLE IF EXISTS public."Ticket";
