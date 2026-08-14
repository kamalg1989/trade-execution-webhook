-- The `strategy` column on backtest_runs already exists (added by an earlier
-- migration, default 'BREAKOUT', no CHECK constraint — confirmed live on the
-- VPS 2026-08-14) with values 'BREAKOUT' | 'PORTFOLIO' | 'POSITIONAL' in use.
-- The Weekly Consolidation Breakout strategy (backtest/weekly_engine.py)
-- reuses that same column with a new value, 'WEEKLY_BREAKOUT' — dispatched
-- from engine.run_backtest(). This migration only adds the one new column
-- that strategy needs for its position-sizing risk %.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS weekly_risk_pct NUMERIC(5,2) DEFAULT 1.0;
