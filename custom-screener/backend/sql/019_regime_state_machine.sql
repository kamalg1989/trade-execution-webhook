-- Option 1: sit out bad regimes, via a forward-known regime STATE MACHINE.
--
-- This is deliberately different from entry_breadth_require_rising (sql/012),
-- which is a day-by-day binary test of breadth against its 20-session average.
-- That kind of gate whipsaws: on a day breadth ticks a hair below its average
-- it stops the whole book, and re-enables the next day. Traders who time the
-- market this way (O'Neil's distribution-day / follow-through-day method being
-- the canonical example) use a STATE with hysteresis instead — you go
-- defensive on confirmed deterioration, and you come back only on confirmed
-- repair. The confirmation requirement is the entire point.
--
-- Signal (all of it known at the OPEN of the decision day, no look-ahead):
--     pct200_t  = % of stocks above their 200SMA on day t
--     ma_t      = trailing regime_ma_days average of pct200 up to day t
--     healthy_t = pct200_t >= ma_t
-- State transitions require regime_confirm_days CONSECUTIVE days agreeing:
--     OFFENSIVE -> DEFENSIVE after N consecutive unhealthy days
--     DEFENSIVE -> OFFENSIVE after N consecutive healthy days
-- so a single noisy day cannot flip the book either way.
--
-- regime_action decides what DEFENSIVE actually does:
--     'block' — take no new entries at all (cash is a position)
--     'half'  — keep trading but at half the normal number of picks
-- Open positions are never touched by this; it gates new entries only, so the
-- regime call can never strand or force-close an existing trade.
--
-- Honest caveat on what this can and cannot prove: the regime signal here is
-- strictly trailing, so it is legitimately tradable. But the years this is
-- meant to protect (2016, 2018, 2019, 2022, 2025 — the ones where every config
-- lost together) are only five observations. A filter that helps in those and
-- costs little elsewhere is worth having; one that merely reshuffles which
-- years win is not, and the per-year table is what decides that, not the total.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS regime_ma_days SMALLINT,
  ADD COLUMN IF NOT EXISTS regime_confirm_days SMALLINT,
  ADD COLUMN IF NOT EXISTS regime_action TEXT;
