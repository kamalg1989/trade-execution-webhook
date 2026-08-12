-- Entry v2: require a BUY POINT as well as a trigger candle, and allow an
-- alternative base-stage allocation ladder.
--
-- entry_v2_buy_points
--   OFF (default): production behaviour — detect_entry_technique() answers
--   "is today's bar actionable" and nothing asks WHERE in the base we are, so a
--   trend bar anywhere qualifies.
--   ON: the candidate must ALSO sit at a recognised buy point (pullback /
--   reverse H&S / high breakout / breakout retest — see backtest/buy_points.py
--   and ENTRY_V2_SPEC.md). Measured to cut survivors from ~100% to ~38%.
--
-- base_stage_ladder
--   'prod' (default): screen_gpt's live {1:1.00, 2:1.00, 3:0.50, 4:0.25}
--   'v2'  : {1:1.00, 2:0.75, 3:0.50, 4:0.25} — monotonic, only Base 2 changes.
--           The ceiling stays 1.00 so no position can exceed 10% of capital or
--           0.25% risk, the same limits production already runs.
--
-- Defaults reproduce production exactly, so every previously recorded run keeps
-- its meaning and remains comparable.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS entry_v2_buy_points BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS base_stage_ladder TEXT NOT NULL DEFAULT 'prod';

-- Which buy point(s) a trade was taken at, for post-hoc analysis. NULL on runs
-- that did not use the gate. Stored as text rather than a lookup table: a bar
-- can satisfy several at once (a high breakout that is also a retest), and the
-- detector deliberately returns all of them rather than collapsing to one.
ALTER TABLE backtest_trades
  ADD COLUMN IF NOT EXISTS buy_points TEXT;
