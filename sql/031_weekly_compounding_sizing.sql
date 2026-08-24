-- WEEKLY_BREAKOUT-only: size positions off running equity (starting capital +
-- cumulative realized P&L at entry time) instead of fixed starting capital.
-- Realized-only (open trades don't count until closed), anti-martingale:
-- profits grow position size, losses shrink it. Both the weekly_risk_pct base
-- and the max_capital_per_trade_pct cap scale with equity.
-- Note: max_capital_per_trade_pct (existing generic column) is also now
-- honoured by the weekly engine — engine default remains 25 when NULL for
-- backward-compat with runs <= #620; the UI weekly presets send 10 explicitly.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS weekly_compounding_sizing BOOLEAN NOT NULL DEFAULT FALSE;
