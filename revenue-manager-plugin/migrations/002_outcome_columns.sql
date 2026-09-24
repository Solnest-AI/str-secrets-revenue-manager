-- Revenue Manager Plugin — Supabase Migration v2 (additive; safe to re-run)
-- Adds 3 nullable outcome columns to pricing_decisions to seed the future
-- learning loop (v2 — NOT built yet). Does not touch 001. Idempotent.
-- Copy this file's contents into: Supabase Dashboard → SQL Editor → New query → Run.

ALTER TABLE public.pricing_decisions
  ADD COLUMN IF NOT EXISTS booked_at date,
  ADD COLUMN IF NOT EXISTS lead_time_days integer,
  ADD COLUMN IF NOT EXISTS price_delta_from_rec numeric;
