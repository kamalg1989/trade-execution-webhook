-- Custom Screener: per-symbol-per-day indicators (TimescaleDB hypertable).
-- Lives in the SAME database as ohlcv_data. symbol TEXT matches ohlcv_data.
CREATE TABLE IF NOT EXISTS stock_indicators (
  symbol             TEXT        NOT NULL,
  indicator_date     DATE        NOT NULL,
  close              NUMERIC(12,2),
  turnover_1m_avg_cr NUMERIC(15,2),
  volume_1m_avg      BIGINT,
  ema_10             NUMERIC(12,2),
  ema_21             NUMERIC(12,2),
  ema_50             NUMERIC(12,2),
  sma_50             NUMERIC(12,2),
  sma_200            NUMERIC(12,2),
  dist_ema_10_pct    NUMERIC(8,2),
  dist_ema_21_pct    NUMERIC(8,2),
  dist_ema_50_pct    NUMERIC(8,2),
  dist_sma_50_pct    NUMERIC(8,2),
  dist_sma_200_pct   NUMERIC(8,2),
  ma_aligned         BOOLEAN,          -- close > EMA50 > SMA200
  price_52w_high     NUMERIC(12,2),
  price_52w_low      NUMERIC(12,2),
  dist_52w_high_pct  NUMERIC(8,2),
  dist_52w_low_pct   NUMERIC(8,2),
  pct_chg_1d         NUMERIC(8,2),
  pct_chg_5d         NUMERIC(8,2),
  pct_chg_1m         NUMERIC(8,2),
  pct_chg_3m         NUMERIC(8,2),
  pct_chg_6m         NUMERIC(8,2),
  pct_chg_1y         NUMERIC(8,2),
  atr_14             NUMERIC(12,2),
  atr_pct            NUMERIC(8,2),
  base_range_20d_pct NUMERIC(8,2),     -- 20-bar (high-low)/low %  (tightness)
  dist_20d_high_pct  NUMERIC(8,2),     -- close vs 20-day high %   (<=0 near breakout)
  vol_ratio_1d       NUMERIC(8,2),     -- today volume / 20d avg
  vol_dryup_ratio    NUMERIC(8,2),     -- base(20) vol / prior(60) vol
  prior_upmove_pct   NUMERIC(8,2),     -- run-up in the 60 bars before the base
  giveback_pct       NUMERIC(8,2),     -- % of prior upmove given back
  ifp_score          NUMERIC(5,3),     -- institutional footprint 0..1 (100d/1.5x/0.60)
  updown_vol_ratio   NUMERIC(8,2),     -- 50d up-vol / down-vol
  obv_slope          NUMERIC(6,3),     -- 50d net signed volume fraction
  bars_available     INT,
  is_new_52w_high    BOOLEAN,      -- did THIS day set a fresh 252-day high (per-day fact)
  is_new_52w_low     BOOLEAN,      -- did THIS day set a fresh 252-day low
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW()
);

-- Hypertable partitioned on indicator_date (same pattern as ohlcv_data).
SELECT create_hypertable('stock_indicators', 'indicator_date',
                         if_not_exists => TRUE,
                         chunk_time_interval => INTERVAL '1 month');

-- Timescale requires the partition column in any UNIQUE constraint.
-- Idempotent: safe to re-run the whole file.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'uq_stock_indicators'
  ) THEN
    ALTER TABLE stock_indicators
      ADD CONSTRAINT uq_stock_indicators UNIQUE (symbol, indicator_date);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_si_symbol_date
  ON stock_indicators (symbol, indicator_date DESC);
