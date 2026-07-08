-- Migration: add IFP + volume-flow columns. Idempotent. Re-run backfill after.
ALTER TABLE stock_indicators
  ADD COLUMN IF NOT EXISTS ifp_score        NUMERIC(5,3),
  ADD COLUMN IF NOT EXISTS updown_vol_ratio NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS obv_slope        NUMERIC(6,3);
