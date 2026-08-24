-- Optional daily-engine entry gate: only allow a new BREAKOUT-strategy entry
-- if the symbol also had a qualifying weekly consolidation-box breakout
-- signal (same definition as the WEEKLY_BREAKOUT strategy, see
-- weekly_breakout.scan_breakout) within a recent lookback window. Motivated
-- by run #589's finding that the weekly strategy's box+volume+10wk-closing-
-- high definition of "breakout" is a coarser, less noisy filter than the
-- daily funnel's own stage-based gates. NULL/false = no filter, every
-- existing run keeps its exact meaning.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS require_weekly_box_breakout BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS weekly_box_lookback_days INT NOT NULL DEFAULT 10;
