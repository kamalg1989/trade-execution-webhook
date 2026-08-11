-- Make backtest_runs able to hold a SECOND strategy, so the positional
-- momentum book is reviewable in the same UI as the breakout book.
--
-- Reusing backtest_runs/backtest_trades rather than new tables is deliberate:
-- the run list, trade log, equity curve, P&L columns, sorting and filtering all
-- already read those tables, so the entire review UI works for the new strategy
-- for free. The alternative — parallel tables — would mean duplicating every
-- one of those surfaces.
--
-- strategy discriminates the two. Existing rows are BREAKOUT by definition,
-- hence the DEFAULT, so nothing already recorded changes meaning.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS strategy TEXT NOT NULL DEFAULT 'BREAKOUT',
  -- Positional-only knobs. NULL for BREAKOUT runs.
  ADD COLUMN IF NOT EXISTS pos_momentum TEXT,          -- pct_chg_3m | 6m | 1y
  ADD COLUMN IF NOT EXISTS pos_rebalance_days SMALLINT,
  ADD COLUMN IF NOT EXISTS pos_top_n SMALLINT,
  ADD COLUMN IF NOT EXISTS pos_buffer_n SMALLINT,
  ADD COLUMN IF NOT EXISTS pos_min_turnover_cr NUMERIC(10,2);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy ON backtest_runs (strategy);

-- How positional trades map onto the breakout-shaped trade row:
--   entry_trigger_price = next-day open used for the fill (pre-slippage)
--   structural_sl       = the stock's SMA200 at signal date. Positional has no
--                         price stop, but SMA200 IS the invalidation level —
--                         losing it drops the name out of the ranked universe
--                         and forces the sell. Storing it keeps risk_per_share
--                         and therefore r_multiple meaningful rather than null.
--   entry_type          = 'MOMENTUM_RANK'
--   exit_reason         = 'RANK_DROP' (fell out of the buffer) or 'WINDOW_END'
