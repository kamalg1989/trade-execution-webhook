-- Realistic Dhan delivery-trade cost model, replacing the old flat
-- brokerage_per_order=20-only assumption (which was actually modeling
-- Dhan's *intraday* rate — Dhan charges zero brokerage on equity delivery,
-- which is what this backtest simulates). Real delivery costs are STT on
-- both legs, stamp duty on the buy leg only, small exchange/SEBI charges,
-- and a flat per-scrip DP charge on the sell leg. Sourced from dhan.co/pricing
-- and chittorgarh.com/brokerage_charges/dhan (see chat for the breakdown).
ALTER TABLE backtest_runs
  ALTER COLUMN brokerage_per_order SET DEFAULT 0.0,
  ADD COLUMN IF NOT EXISTS stt_pct              NUMERIC(5,3) NOT NULL DEFAULT 0.100,
  ADD COLUMN IF NOT EXISTS stamp_duty_pct       NUMERIC(5,3) NOT NULL DEFAULT 0.015,
  ADD COLUMN IF NOT EXISTS exchange_charges_pct NUMERIC(5,4) NOT NULL DEFAULT 0.0030,
  ADD COLUMN IF NOT EXISTS dp_charge            NUMERIC(6,2) NOT NULL DEFAULT 14.75;

-- Only affects the DEFAULT for future INSERTs — existing runs keep whatever
-- brokerage_per_order they were actually created with (historical accuracy).
