-- How often the breakout funnel generates signals.
--
-- Production scans nightly and alerts the top 3. The portfolio work raised an
-- obvious question it could not answer: is the breakout edge destroyed by
-- TRADING it every day, rather than by the picks themselves? A weekly or
-- monthly scan of the identical funnel isolates that.
--
-- signal_cadence   daily (production today) | weekly | monthly
-- signal_scan_day  first | last session of the period
--
-- Only SIGNAL GENERATION is gated by this. Exits, entry fills and daily
-- mark-to-market continue every session regardless — a weekly scan must never
-- imply a weekly stop-loss, which would be a materially more dangerous system
-- than the one being tested.
--
-- Scan day is a PHASE choice, not an information one: a scan on any day sees
-- everything up to that day and nothing beyond it. 'last' is the default
-- because it matches a real weekend/month-end review — decide with the whole
-- period visible, act on the next open.
ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS signal_cadence TEXT NOT NULL DEFAULT 'daily',
  ADD COLUMN IF NOT EXISTS signal_scan_day TEXT NOT NULL DEFAULT 'last';

-- Existing rows are all nightly scans, which the DEFAULT preserves, so nothing
-- already recorded changes meaning.
