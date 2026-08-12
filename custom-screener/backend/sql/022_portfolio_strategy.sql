-- Make the continuous portfolio backtest reviewable in the UI.
--
-- PORTFOLIO is a third strategy alongside BREAKOUT and POSITIONAL. It differs
-- from POSITIONAL in ONE way that matters for the schema: it is a single
-- continuous simulation with compounding capital and portfolio-level risk
-- controls, so its headline numbers are path metrics (CAGR, ulcer index, worst
-- rolling 12-month return) rather than a P&L total. Those have nowhere to live
-- on backtest_runs today, and recomputing them in the summary endpoint from
-- backtest_trades is not possible: the equity curve depends on daily
-- mark-to-market of the whole book, which the trade rows do not carry.
--
-- So the engine's metrics are stored on the run row itself. Trades are still
-- written to backtest_trades exactly as the other strategies do, so the trade
-- log, filters and search keep working unchanged.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS pf_vol_mode TEXT NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS pf_vol_floor NUMERIC(5,2),
  ADD COLUMN IF NOT EXISTS pf_max_per_stock_pct NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS pf_max_per_sector_pct NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS pf_max_stocks_per_sector SMALLINT,
  ADD COLUMN IF NOT EXISTS pf_require_sector BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS pf_dd_throttle_at NUMERIC(5,3),
  -- Path metrics, computed by portfolio_engine and stored rather than derived.
  ADD COLUMN IF NOT EXISTS pf_cagr_pct NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS pf_max_dd_pct NUMERIC(6,2),
  ADD COLUMN IF NOT EXISTS pf_ulcer NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS pf_worst_12m_pct NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS pf_martin NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS pf_turnover_per_yr NUMERIC(8,2),
  ADD COLUMN IF NOT EXISTS pf_avg_exposure NUMERIC(5,3),
  ADD COLUMN IF NOT EXISTS pf_final_equity NUMERIC(16,2),
  -- {year: return%} and the daily curve, for the UI chart.
  ADD COLUMN IF NOT EXISTS pf_calendar JSONB,
  ADD COLUMN IF NOT EXISTS pf_equity_curve JSONB;

-- Existing rows are BREAKOUT/POSITIONAL and keep pf_* NULL, which the UI reads
-- as "this run has no path metrics" rather than as zeros.
