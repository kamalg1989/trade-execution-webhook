-- Migration: add group-1 (BAU-parity) indicator columns to an existing table.
-- Idempotent. Run once, then re-run the backfill to populate the new columns.
ALTER TABLE stock_indicators
  ADD COLUMN IF NOT EXISTS ema_50             NUMERIC(12,2),
  ADD COLUMN IF NOT EXISTS dist_ema_50_pct    NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS ma_aligned         BOOLEAN,
  ADD COLUMN IF NOT EXISTS atr_pct            NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS base_range_20d_pct NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS dist_20d_high_pct  NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS vol_ratio_1d       NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS vol_dryup_ratio    NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS prior_upmove_pct   NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS giveback_pct       NUMERIC(8,2);
