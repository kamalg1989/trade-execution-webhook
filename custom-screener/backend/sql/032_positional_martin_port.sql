-- 2026-08-20 Martin-port: profit-armed positional stop + equity-curve throttle.
-- All NULL = inert; every prior run reproduces byte-identically.
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS pos_sl_arm_pct NUMERIC(6,2);
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS pos_eq_throttle_dd_pct NUMERIC(6,2);
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS pos_eq_throttle_cut NUMERIC(4,2);
