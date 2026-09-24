-- Revenue Manager Plugin — Supabase Migration v1
-- Run this ONCE in your own Supabase project before using the skill.
-- Copy this file's contents into: Supabase Dashboard → SQL Editor → New query → Run.

-- 1. market_snapshots — point-in-time market/comp data (one row per property per snapshot_date)
CREATE TABLE IF NOT EXISTS public.market_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz DEFAULT now(),
  property_id text NOT NULL,
  snapshot_date date NOT NULL,
  occupancy_pct numeric,
  avg_comp_rate numeric,
  demand_score numeric,
  raw_data jsonb DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS market_snapshots_property_date_idx
  ON public.market_snapshots (property_id, snapshot_date);

-- 2. pricelabs_change_log — append-only log of every change pushed to the pricing tool
CREATE TABLE IF NOT EXISTS public.pricelabs_change_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz DEFAULT now(),
  property_name text NOT NULL,
  listing_id text,
  change_type text NOT NULL,
  field_changed text NOT NULL,
  old_value text,
  new_value text NOT NULL,
  reason text NOT NULL,
  changed_by text DEFAULT 'Claude',
  notes text
);

CREATE INDEX IF NOT EXISTS pricelabs_change_log_listing_idx
  ON public.pricelabs_change_log (listing_id, created_at DESC);

-- 3. pricing_decisions — analysis + recommendation per property per decision_date
CREATE TABLE IF NOT EXISTS public.pricing_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz DEFAULT now(),
  property_id text NOT NULL,
  decision_date date NOT NULL,
  base_price numeric NOT NULL,
  final_price numeric NOT NULL,
  strategy text,
  signals jsonb DEFAULT '[]'::jsonb,
  reasoning text,
  outcome text
);

CREATE INDEX IF NOT EXISTS pricing_decisions_property_date_idx
  ON public.pricing_decisions (property_id, decision_date DESC);

-- 4. property_config — per-property settings (markup %, targets, season definitions)
CREATE TABLE IF NOT EXISTS public.property_config (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  property_id text NOT NULL UNIQUE,
  display_name text,
  base_price numeric,
  min_price numeric,
  max_price numeric,
  settings jsonb DEFAULT '{}'::jsonb
);

-- Auto-update updated_at on property_config changes
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS property_config_updated_at ON public.property_config;
CREATE TRIGGER property_config_updated_at
  BEFORE UPDATE ON public.property_config
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Enable RLS (Supabase default — adjust policies to your auth model)
ALTER TABLE public.market_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pricelabs_change_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pricing_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.property_config ENABLE ROW LEVEL SECURITY;

-- Example: allow the service role full access (service role bypasses RLS anyway,
-- but this makes intent explicit). Tighten for your multi-user setup.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'market_snapshots' AND policyname = 'service_all') THEN
    CREATE POLICY service_all ON public.market_snapshots FOR ALL USING (true) WITH CHECK (true);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'pricelabs_change_log' AND policyname = 'service_all') THEN
    CREATE POLICY service_all ON public.pricelabs_change_log FOR ALL USING (true) WITH CHECK (true);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'pricing_decisions' AND policyname = 'service_all') THEN
    CREATE POLICY service_all ON public.pricing_decisions FOR ALL USING (true) WITH CHECK (true);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'property_config' AND policyname = 'service_all') THEN
    CREATE POLICY service_all ON public.property_config FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
