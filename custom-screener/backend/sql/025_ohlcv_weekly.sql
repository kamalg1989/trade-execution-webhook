-- Persistent weekly OHLCV bars, resampled once from ohlcv_data (daily) --
-- used by the Weekly Consolidation Breakout strategy (backtest/weekly_breakout.py).
-- Deliberately a real table, not a view/per-run computation: weekly bar
-- construction is universal (doesn't depend on any backtest run's config,
-- unlike Stage 1/Stage 2 overrides elsewhere in this backtest engine), so
-- computing it once and reusing it across every future weekly-strategy run
-- avoids re-resampling ~4M daily rows on every run.
--
-- week_start = Monday of the ISO week (date_trunc('week', ...) truncates to
-- Monday). week_end = the actual last trading day that week (usually Friday,
-- but holidays can make it earlier) -- this is what signal-generation code
-- should compare against a requested "as of" date, not week_start.
CREATE TABLE IF NOT EXISTS ohlcv_weekly (
    symbol      TEXT NOT NULL,
    week_start  DATE NOT NULL,
    week_end    DATE NOT NULL,
    open        NUMERIC(12,2) NOT NULL,
    high        NUMERIC(12,2) NOT NULL,
    low         NUMERIC(12,2) NOT NULL,
    close       NUMERIC(12,2) NOT NULL,
    volume      BIGINT,
    PRIMARY KEY (symbol, week_start)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_weekly_symbol_end ON ohlcv_weekly (symbol, week_end);

INSERT INTO ohlcv_weekly (symbol, week_start, week_end, open, high, low, close, volume)
SELECT
    symbol,
    date_trunc('week', time)::date AS week_start,
    MAX(time::date) AS week_end,
    (ARRAY_AGG(open ORDER BY time ASC))[1] AS open,
    MAX(high) AS high,
    MIN(low) AS low,
    (ARRAY_AGG(close ORDER BY time DESC))[1] AS close,
    SUM(volume)::BIGINT AS volume
FROM ohlcv_data
GROUP BY symbol, date_trunc('week', time)
ON CONFLICT (symbol, week_start) DO NOTHING;
