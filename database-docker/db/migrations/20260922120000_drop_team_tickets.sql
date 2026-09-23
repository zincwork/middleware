-- Removes the "TeamTickets" table.
--
-- The first version of the ticket layer mapped Shortcut teams onto Middleware
-- teams. That was the wrong axis: a Middleware Team is a group of
-- REPOSITORIES, and Zinc's squads share repositories — which is the whole
-- reason the GitHub Team filter exists. The mapping now lives in
-- web-server/config/github_teams.json alongside each GitHub team's member
-- logins, so one dropdown drives both the PR metrics and the ticket metrics.
--
-- This migration exists because editing an already-applied migration file has
-- no effect: dbmate records applied versions in schema_migrations and will not
-- re-run one. A database that took the five-table version needs this to catch
-- up with a fresh install, which now creates four.
--
-- Safe: the table was only ever written by the superseded setup script, and
-- nothing reads it now. IF EXISTS means this is a no-op on a fresh database
-- that never had it.
--
-- The down section recreates it empty, purely so the migration is reversible.

-- migrate:up

DROP TABLE IF EXISTS public."TeamTickets";

-- migrate:down

CREATE TABLE IF NOT EXISTS public."TeamTickets" (
    team_id uuid NOT NULL,
    provider character varying NOT NULL,
    provider_team_id character varying NOT NULL,
    provider_team_name character varying,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT "TeamTickets_pkey" PRIMARY KEY (team_id, provider, provider_team_id)
);
