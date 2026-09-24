-- Restrict the policies shipped by earlier versions to the service role.
-- Run after 001 and 002. Safe to re-run; does not change any data or other policies.
-- Existing installs need this migration because CREATE TABLE IF NOT EXISTS and
-- the guarded CREATE POLICY statements in 001 do not repair existing policies.
DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'market_snapshots', 'pricelabs_change_log', 'pricing_decisions', 'property_config'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
    IF EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = 'public' AND tablename = table_name AND policyname = 'service_all'
    ) THEN
      EXECUTE format('ALTER POLICY service_all ON public.%I TO service_role', table_name);
    ELSE
      EXECUTE format(
        'CREATE POLICY service_all ON public.%I FOR ALL TO service_role USING (true) WITH CHECK (true)',
        table_name
      );
    END IF;
  END LOOP;
END $$;
