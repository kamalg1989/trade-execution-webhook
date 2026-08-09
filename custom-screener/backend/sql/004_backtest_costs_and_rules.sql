-- Run-level config for the new exit-rule set + cost realism (see
-- BACKTEST_ENGINE_SPEC.md and the exit-rules review in chat history).
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS safety_sl_pct      NUMERIC(5,2) NOT NULL DEFAULT 8.0,
  ADD COLUMN IF NOT EXISTS slippage_pct       NUMERIC(5,3) NOT NULL DEFAULT 0.10,
  ADD COLUMN IF NOT EXISTS brokerage_per_order NUMERIC(8,2) NOT NULL DEFAULT 20.0,
  ADD COLUMN IF NOT EXISTS chandelier_atr_mult NUMERIC(4,2) NOT NULL DEFAULT 3.0;

-- Gross (frictionless, pre-slippage/brokerage) P&L tracked alongside the
-- existing realized_pnl (which is now NET, after costs) so the UI can show
-- both and make the cost drag visible.
ALTER TABLE backtest_trades
  ADD COLUMN IF NOT EXISTS gross_pnl NUMERIC(15,2);
