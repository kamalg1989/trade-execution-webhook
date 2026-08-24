CREATE TABLE IF NOT EXISTS sensex_options_ohlcv (
    time TIMESTAMPTZ NOT NULL,
    strike_label TEXT NOT NULL,   -- e.g. 'ATM', 'ATM+2', 'ATM-3'
    option_type TEXT NOT NULL,    -- CE or PE
    strike_price NUMERIC(10,2),
    spot NUMERIC(10,2),
    open NUMERIC(10,2), high NUMERIC(10,2), low NUMERIC(10,2), close NUMERIC(10,2),
    volume BIGINT, oi BIGINT, iv NUMERIC(8,3),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
SELECT create_hypertable('sensex_options_ohlcv', 'time', if_not_exists => TRUE, chunk_time_interval => INTERVAL '30 days');
CREATE UNIQUE INDEX IF NOT EXISTS uq_sensex_opt ON sensex_options_ohlcv (strike_label, option_type, time);
CREATE INDEX IF NOT EXISTS idx_sensex_opt_lookup ON sensex_options_ohlcv (strike_label, option_type, time DESC);
GRANT ALL PRIVILEGES ON TABLE sensex_options_ohlcv TO market_data_user;
