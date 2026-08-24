-- ============================================================
-- INTRADAY OHLCV SCHEMA (5m / 15m candles)
-- ============================================================
-- Purpose: 2 years of 5m and 15m OHLCV for NIFTY 500, for
-- intraday strategy backtesting (ORB, VWAP, momentum, etc.)
-- ============================================================

CREATE TABLE IF NOT EXISTS intraday_ohlcv (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,  -- '5m' or '15m'
    open NUMERIC(10, 2) NOT NULL,
    high NUMERIC(10, 2) NOT NULL,
    low NUMERIC(10, 2) NOT NULL,
    close NUMERIC(10, 2) NOT NULL,
    volume BIGINT NOT NULL,
    data_source TEXT DEFAULT 'dhan',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE intraday_ohlcv IS 'Intraday OHLCV candles (5m/15m) for NSE equities, used for intraday strategy backtesting.';
COMMENT ON COLUMN intraday_ohlcv.time IS 'Candle timestamp in IST (stored as TIMESTAMPTZ)';
COMMENT ON COLUMN intraday_ohlcv.timeframe IS 'Candle granularity: 5m or 15m';

-- Hypertable (1 week chunks — much higher row density than daily)
SELECT create_hypertable(
    'intraday_ohlcv',
    'time',
    if_not_exists => TRUE,
    chunk_time_interval => INTERVAL '7 days'
);

-- Prevent duplicate candles on re-ingestion
CREATE UNIQUE INDEX IF NOT EXISTS uq_intraday_symbol_tf_time
ON intraday_ohlcv (symbol, timeframe, time);

-- Primary query pattern: symbol + timeframe + time range
CREATE INDEX IF NOT EXISTS idx_intraday_symbol_tf_time
ON intraday_ohlcv (symbol, timeframe, time DESC);

-- Bulk scan across symbols for a given time + timeframe
CREATE INDEX IF NOT EXISTS idx_intraday_tf_time
ON intraday_ohlcv (timeframe, time DESC);

-- Ingestion tracking (mirrors ingestion_log pattern)
CREATE TABLE IF NOT EXISTS intraday_ingestion_log (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    from_date DATE NOT NULL,
    to_date DATE NOT NULL,
    records_inserted BIGINT DEFAULT 0,
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_intraday_ingestion_status
ON intraday_ingestion_log (status);

CREATE INDEX IF NOT EXISTS idx_intraday_ingestion_symbol_tf
ON intraday_ingestion_log (symbol, timeframe);

-- Compression for data older than 14 days
SELECT add_compression_policy('intraday_ohlcv', INTERVAL '14 days', if_not_exists => TRUE);

-- Permissions
GRANT ALL PRIVILEGES ON TABLE intraday_ohlcv TO market_data_user;
GRANT ALL PRIVILEGES ON TABLE intraday_ingestion_log TO market_data_user;
GRANT ALL PRIVILEGES ON SEQUENCE intraday_ingestion_log_id_seq TO market_data_user;
