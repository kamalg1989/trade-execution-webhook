-- Daily pipeline-integrity telemetry for the paper book.
-- ADDITIVE ONLY. Drop with:  DROP TABLE paper_telemetry;

-- Slippage cannot be measured unless the SIGNAL price is stored separately from
-- the FILL price. entry_price already has the 0.32% cost baked in, so on its own
-- it can never answer "what did execution actually cost us".
ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS signal_price  numeric(12,2);
ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS fill_price_raw numeric(12,2);
ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS slippage_bps  numeric(10,2);
ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS signal_date   date;

CREATE TABLE IF NOT EXISTS paper_telemetry (
    d                  date PRIMARY KEY,
    -- pipeline liveness
    indicator_date     date,        -- newest row in stock_indicators
    ohlcv_date         date,        -- newest row in ohlcv_data
    staleness_days     integer,     -- calendar days since indicator_date
    pipeline_ok        boolean,     -- indicator_date advanced as expected
    -- book coverage: the silent-failure detector
    n_open             integer,
    n_quoted           integer,     -- holdings with a fresh quote today
    n_missing_quote    integer,     -- holdings marked at STALE price
    -- corporate-action / data-integrity detectors
    n_price_mismatch   integer,     -- |indicators.close - ohlcv.close| > 5%
    n_extreme_move     integer,     -- |1-day move| > 20% on a holding
    worst_move_symbol  text,
    worst_move_pct     numeric(8,2),
    -- universe health
    n_candidates       integer,     -- gate survivors TODAY (not just at rebalance)
    -- book state
    equity             numeric(16,2),
    drawdown_pct       numeric(8,3),
    -- anything that needs a human
    alerts             text,
    created_at         timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_paper_telemetry_alerts
    ON paper_telemetry(d) WHERE alerts IS NOT NULL AND alerts <> '';
