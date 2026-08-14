-- earnings_fundamentals had NO indexes at all (confirmed via \d on the VPS,
-- 2026-08-14) — every fundamentals_pass() lookup in weekly_breakout.py
-- (WHERE symbol = $1 AND broadcast_date < $2 ORDER BY period_to DESC LIMIT 2)
-- was a full sequential scan of the whole table (80k+ rows) per call. With
-- thousands of breakout signals to check across a 2016-2026 run, this made
-- the fundamentals-filter phase the dominant cost of the whole backtest.
-- Applied directly on the VPS during the first full run (id 575) to unblock
-- it; this migration file exists so the fix isn't lost/re-diagnosed later.
CREATE INDEX IF NOT EXISTS idx_earnings_fundamentals_symbol_period
  ON earnings_fundamentals (symbol, period_to DESC);
