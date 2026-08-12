-- Give the positional book a stop-loss.
--
-- Until now it had NONE: the only exit was at the next rebalance (rank drop or
-- lost SMA200), which is the whole reason it drew down ~42-45%. A dedicated
-- sweep (positional_sweep.py) showed a daily-checked stop is not merely a risk
-- control here but a net improvement — a fixed 15% stop raised total return
-- while cutting maxDD from 42% to 33% — so it belongs in the persisted engine
-- and the UI, not only in the research script.
--
-- pos_sl_mode:
--   none    exit only at rebalance          (the previous, and still default,
--                                            behaviour — existing rows keep
--                                            their meaning)
--   fixed   pos_sl_pct below the ENTRY price
--   trail   pos_sl_pct below the highest close since entry
--   sma200 | sma50 | ema50 | ema21
--           daily close below that moving average. Same mechanism at four
--           speeds (ema21 fastest -> sma200 slowest), which makes it an ordered
--           axis where a plateau is evidence and an isolated winner is not.
--
-- pos_sl_pct is only read for fixed/trail; it is ignored by the MA modes.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS pos_sl_mode TEXT NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS pos_sl_pct NUMERIC(6,2) NOT NULL DEFAULT 0;

-- Stop exits are recorded on backtest_trades with exit_reason:
--   'SL_FIXED' | 'SL_TRAIL' | 'SL_SMA200' | 'SL_SMA50' | 'SL_EMA50' | 'SL_EMA21'
-- so the trade log distinguishes "stopped out" from "rotated out" (RANK_DROP)
-- without needing a separate column.
