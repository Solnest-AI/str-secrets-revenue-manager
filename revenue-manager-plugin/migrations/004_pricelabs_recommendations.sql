-- Revenue Manager Plugin — Supabase Migration v4 (additive; safe to re-run)
-- PRD D14 (Ryan-stated 2026-09-21): PriceLabs actions and nudges are fetched every run
-- and PERSISTED, latest-wins, so the last PriceLabs recommendation is always on hand
-- between runs. They are one input to the analysis, never its basis.
--
-- Latest-wins is implemented as supersession, not deletion: a new pull marks the
-- listing's previous rows superseded and inserts the fresh set. History stays for the
-- learning loop; "current" is simply WHERE superseded_at IS NULL.
-- Idempotent. Does not touch 001-003.
-- Copy this file's contents into: Supabase Dashboard → SQL Editor → New query → Run.

CREATE TABLE IF NOT EXISTS public.pricelabs_recommendations (
  id             bigserial PRIMARY KEY,
  listing_id     text        NOT NULL,
  pms            text        NOT NULL,
  kind           text        NOT NULL CHECK (kind IN ('nudge', 'action')),
  external_id    text        NOT NULL,   -- nudge_id, or action_type for actions
  scope          text        NOT NULL CHECK (scope IN ('this-listing', 'other-listing')),
  owner_listing  text,                   -- the listing PriceLabs says it belongs to
  payload        jsonb       NOT NULL,   -- the row as flattened by reduce_customizations
  pulled_at      timestamptz NOT NULL,
  run_id         text,
  superseded_at  timestamptz             -- NULL = this is the current recommendation
);

CREATE INDEX IF NOT EXISTS pricelabs_recommendations_current
  ON public.pricelabs_recommendations (listing_id, kind)
  WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS pricelabs_recommendations_pulled
  ON public.pricelabs_recommendations (listing_id, pulled_at DESC);

COMMENT ON TABLE public.pricelabs_recommendations IS
  'PriceLabs actions and nudges, one row each, latest-wins via superseded_at. PRD D14.';
