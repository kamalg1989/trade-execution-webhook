-- Configurable position sizing (backtest-only).
--
-- funnel._size_qty() mirrors screen_gpt.create_trade()'s formula, whose two
-- sizing constants are hardcoded literals in production:
--     qty_risk = capital * 0.0025 * stage_mult / risk_per_share   (0.25% risk/trade)
--     qty_cap  = capital * 0.10   * stage_mult / entry            (10% capital/trade)
-- and qty = min(qty_risk, qty_cap).
--
-- 0.25% risk per trade is very conservative relative to the 1-2%/trade that is
-- standard for swing systems, and it interacts with cost drag: measured on run
-- #130, the average position is ~Rs 22k and costs ~Rs 115 round-trip, of which
-- only ~14% (the flat Rs 14.75 DP charge) is fixed -- the other ~86% is
-- proportional (0.2% slippage + 0.2% STT + stamp + exchange). So bigger
-- positions amortize the flat slice but cannot reduce the proportional slice;
-- raising risk/trade scales P&L and drawdown together, and only modestly
-- improves cost EFFICIENCY. These knobs exist so that trade-off can actually
-- be measured rather than assumed.
--
-- NULL (default) on either column = use production's literal, so an untouched
-- run reproduces existing behavior exactly.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS risk_per_trade_pct NUMERIC(6,3),
  ADD COLUMN IF NOT EXISTS max_capital_per_trade_pct NUMERIC(6,3);
