-- Strategy 2 (SQUEEZE_BREAKOUT) + Strategy 3 (RSI_REVERSION) run-config
-- columns. See custom-screener/backend/backtest/funnel_squeeze.py and
-- funnel_rsi.py for the strategies these configure. Both strategies reuse
-- backtest_runs.exit_config / max_holding_days / risk_per_trade_pct /
-- max_capital_per_trade_pct (already generic columns) — these four are the
-- only genuinely strategy-specific numeric knobs that need their own column.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS squeeze_volume_multiplier NUMERIC(4,2) NOT NULL DEFAULT 1.5,
  ADD COLUMN IF NOT EXISTS rsi_entry_threshold NUMERIC(5,2) NOT NULL DEFAULT 35.0,
  ADD COLUMN IF NOT EXISTS rsi_stop_pct NUMERIC(5,2) NOT NULL DEFAULT 4.5,
  ADD COLUMN IF NOT EXISTS rsi_target_pct NUMERIC(5,2) NOT NULL DEFAULT 5.0;
