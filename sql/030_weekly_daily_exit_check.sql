-- WEEKLY_BREAKOUT-only: check stop-breach daily instead of only at week-end
-- (the MACD ratchet level itself still only updates weekly). See
-- weekly_simulator.check_daily_stop_breach / update_macd_ratchet and
-- weekly_engine.run_weekly_backtest's Phase C docstring.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS weekly_daily_exit_check BOOLEAN NOT NULL DEFAULT FALSE;
