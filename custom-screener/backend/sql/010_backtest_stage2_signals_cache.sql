-- Config-aware cache for Stage 2 (base-stage classification + entry-technique
-- detection) results, keyed by (symbol, signal_date, config_hash) instead of
-- just (symbol, signal_date) like backtest_quant_signals.
--
-- Why a separate table/key instead of reusing backtest_quant_signals: that
-- table assumes Stage 2 is always computed at screen_gpt.py's fixed
-- production constants, so every row is valid for every run. Once Stage 2
-- became overridable (sql/008), a run with different override values would
-- get a *different* passed/entry/sl/base_stage result for the same
-- (symbol, date) -- sharing one cache row across configs would silently
-- return the wrong config's result. config_hash (sha256 of the 9 resolved
-- override values, see funnel_stage2.py's _config_hash()) partitions the
-- cache so two runs only ever share a row when their effective Stage 2
-- settings are byte-for-byte identical -- same correctness guarantee as the
-- previous "never cache" approach, just narrower instead of blanket.
CREATE TABLE IF NOT EXISTS backtest_stage2_signals_cache (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    signal_date     DATE NOT NULL,
    config_hash     TEXT NOT NULL,
    passed          BOOLEAN NOT NULL,
    entry           NUMERIC(12,2),
    sl              NUMERIC(12,2),
    entry_type      TEXT,
    base_stage      INTEGER,
    risk_per_share  NUMERIC(12,4),
    target          NUMERIC(12,2),
    ifp_score       NUMERIC(6,3),
    base_range_pct  NUMERIC(8,3),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_backtest_stage2_signals_cache
  ON backtest_stage2_signals_cache (symbol, signal_date, config_hash);

-- Lookup pattern is always "give me every symbol for this (date, config_hash)"
CREATE INDEX IF NOT EXISTS idx_backtest_stage2_signals_cache_date_config
  ON backtest_stage2_signals_cache (config_hash, signal_date);
