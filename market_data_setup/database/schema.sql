-- ============================================================
-- Market Data PostgreSQL + TimescaleDB Schema
-- ============================================================
-- Created: 2026-06-28
-- Purpose: 15 years of daily OHLCV for 2000 NSE stocks
-- Storage: ~200 MB compressed
-- ============================================================

-- Enable TimescaleDB extension (run once)
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================
-- MAIN HYPERTABLE: OHLCV DATA (Time-series optimized)
-- ============================================================
CREATE TABLE IF NOT EXISTS ohlcv_data (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    open NUMERIC(10, 2) NOT NULL,
    high NUMERIC(10, 2) NOT NULL,
    low NUMERIC(10, 2) NOT NULL,
    close NUMERIC(10, 2) NOT NULL,
    volume BIGINT NOT NULL,
    oi BIGINT,
    data_source TEXT DEFAULT 'dhan',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Comment for documentation
COMMENT ON TABLE ohlcv_data IS 'Daily OHLCV candles for NSE equities. Time-series hypertable.';
COMMENT ON COLUMN ohlcv_data.time IS 'Trading date in IST timezone';
COMMENT ON COLUMN ohlcv_data.symbol IS 'NSE trading symbol (e.g., INFY, TCS)';
COMMENT ON COLUMN ohlcv_data.oi IS 'Open Interest (if available from Dhan)';

-- Convert to TimescaleDB hypertable (time-series optimizations)
SELECT create_hypertable(
    'ohlcv_data',
    'time',
    if_not_exists => TRUE,
    chunk_time_interval => INTERVAL '1 month'
);

-- ============================================================
-- INDEXES (Critical for query performance)
-- ============================================================

-- Primary index: symbol + time (most common query pattern)
CREATE INDEX IF NOT EXISTS idx_symbol_time
ON ohlcv_data (symbol, time DESC);

-- Secondary index: time + symbol (bulk queries)
CREATE INDEX IF NOT EXISTS idx_time_symbol
ON ohlcv_data (time DESC, symbol);

-- For range queries on time only
CREATE INDEX IF NOT EXISTS idx_time
ON ohlcv_data (time DESC);

-- For finding specific symbol
CREATE INDEX IF NOT EXISTS idx_symbol
ON ohlcv_data (symbol);

-- ============================================================
-- SYMBOLS METADATA TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS symbols_meta (
    symbol TEXT PRIMARY KEY,
    isin TEXT UNIQUE,
    security_name TEXT NOT NULL,
    sector TEXT,
    list_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    dhan_security_id TEXT UNIQUE,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE symbols_meta IS 'Metadata for NSE symbols';
COMMENT ON COLUMN symbols_meta.dhan_security_id IS 'Dhan API security ID for historical data fetching';

-- Index for symbol lookups
CREATE INDEX IF NOT EXISTS idx_symbols_active
ON symbols_meta (is_active) WHERE is_active = TRUE;

-- Index for sector analysis
CREATE INDEX IF NOT EXISTS idx_symbols_sector
ON symbols_meta (sector);

-- ============================================================
-- INGESTION LOG TABLE (Track import progress)
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_log (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    from_date DATE NOT NULL,
    to_date DATE NOT NULL,
    records_inserted BIGINT DEFAULT 0,
    status TEXT DEFAULT 'pending', -- pending, completed, failed
    error_message TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    duration_seconds INTEGER
);

COMMENT ON TABLE ingestion_log IS 'Track data ingestion progress and history';
COMMENT ON COLUMN ingestion_log.status IS 'pending = queued, completed = success, failed = error';

CREATE INDEX IF NOT EXISTS idx_ingestion_status
ON ingestion_log (status);

CREATE INDEX IF NOT EXISTS idx_ingestion_symbol
ON ingestion_log (symbol);

-- ============================================================
-- COMPRESSION POLICY (TimescaleDB - saves ~70% disk)
-- ============================================================
-- Compress data older than 30 days automatically
SELECT add_compression_policy('ohlcv_data', INTERVAL '30 days', if_not_exists => TRUE);

-- ============================================================
-- RETENTION POLICY (Optional - auto-delete old compressed data)
-- ============================================================
-- Keep raw + compressed data indefinitely (15 years = 3,750 records per symbol)
-- Uncomment if you want to auto-delete after 20 years:
-- SELECT add_retention_policy('ohlcv_data', INTERVAL '20 years', if_not_exists => TRUE);

-- ============================================================
-- CONTINUITY AGGREGATE (for efficient queries)
-- ============================================================
-- Creates materialized aggregate for fast downsampling
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_daily_agg AS
SELECT
    time_bucket('1 day', time) as bucket,
    symbol,
    first(open, time) as open,
    max(high) as high,
    min(low) as low,
    last(close, time) as close,
    sum(volume) as volume
FROM ohlcv_data
GROUP BY bucket, symbol;

-- Index on materialized view
CREATE INDEX IF NOT EXISTS idx_agg_symbol_bucket
ON ohlcv_daily_agg (symbol, bucket DESC);

-- ============================================================
-- PERMISSIONS (For market_data_user)
-- ============================================================
-- Grant all permissions to market_data_user
GRANT ALL PRIVILEGES ON TABLE ohlcv_data TO market_data_user;
GRANT ALL PRIVILEGES ON TABLE symbols_meta TO market_data_user;
GRANT ALL PRIVILEGES ON TABLE ingestion_log TO market_data_user;
GRANT ALL PRIVILEGES ON TABLE ohlcv_daily_agg TO market_data_user;

-- Grant default privileges for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO market_data_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO market_data_user;

-- ============================================================
-- INITIAL DATA LOAD (Optional: add common sectors)
-- ============================================================
INSERT INTO symbols_meta (symbol, security_name, sector, is_active)
VALUES
    ('INFY', 'Infosys Limited', 'IT', TRUE),
    ('TCS', 'Tata Consultancy Services', 'IT', TRUE),
    ('RELIANCE', 'Reliance Industries Limited', 'Energy', TRUE),
    ('HDFCBANK', 'HDFC Bank Limited', 'Banking', TRUE),
    ('ICICIBANK', 'ICICI Bank Limited', 'Banking', TRUE),
    ('SBIN', 'State Bank of India', 'Banking', TRUE),
    ('BHARTIARTL', 'Bharti Airtel Limited', 'Telecom', TRUE),
    ('WIPRO', 'Wipro Limited', 'IT', TRUE),
    ('AXISBANK', 'Axis Bank Limited', 'Banking', TRUE),
    ('HINDUNILVR', 'Hindustan Unilever Limited', 'FMCG', TRUE)
ON CONFLICT (symbol) DO NOTHING;

-- ============================================================
-- VERIFICATION QUERIES (Test after schema creation)
-- ============================================================

-- Check hypertable status
-- SELECT hypertable_name, num_dimensions, num_chunks FROM timescaledb_information.hypertables;

-- Check table sizes
-- SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) as size
-- FROM pg_class WHERE relname IN ('ohlcv_data', 'symbols_meta');

-- Count records
-- SELECT COUNT(*) as total_records FROM ohlcv_data;

-- Check indexes
-- SELECT indexname FROM pg_indexes WHERE tablename = 'ohlcv_data';

-- ============================================================
-- END OF SCHEMA
-- ============================================================
